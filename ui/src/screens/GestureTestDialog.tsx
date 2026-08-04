import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
  type MouseEvent,
  type RefObject,
} from "react";

import type {
  GestureKind,
  GestureTestEvent,
  LibraryGesture,
  ServerEvent,
} from "../lib/types";
import type { AirControlClient, ConnectionState } from "../lib/ws";

type TestClient = Pick<
  AirControlClient,
  "on" | "onPreviewFrame" | "onState" | "request" | "send"
>;

/** Unsuccessful attempts before the "Skip for now" escape hatch appears. */
const SKIP_THRESHOLD = 3;

type DiagnosticTone = "info" | "success" | "warning";
type ActionBusy = "delete" | null;

/** Identifies the gesture under test, and what it is mapped to -- resolved
 * by the caller (Gestures.tsx already has both) so this dialog never needs
 * its own library fetch. */
export interface GestureTestTarget {
  id: number;
  name: string;
  kind: GestureKind;
  actionDescription: string;
}

interface GestureTestDialogProps {
  client: TestClient;
  connectionState: ConnectionState;
  gesture: GestureTestTarget;
  returnFocusRef: RefObject<HTMLElement>;
  /**
   * The caller's already-loaded gesture library, used only to resolve a
   * confused-with gesture's name (e.g. "That matched 'X' instead"). Never
   * refetched here -- a passive, best-effort lookup.
   */
  libraryGestures?: readonly LibraryGesture[] | null;
  /** Every exit path funnels here; the caller closes/unmounts the dialog. */
  onClose: () => void;
  /** The gesture tested poorly: it has been deleted, and the caller should
   * reopen the recording dialog so the user can record it again. */
  onReRecord: (gesture: GestureTestTarget) => void;
}

function showDialog(dialog: HTMLDialogElement | null): void {
  if (dialog === null || dialog.open) {
    return;
  }
  if (typeof dialog.showModal === "function") {
    try {
      dialog.showModal();
      return;
    } catch {
      // Some embedded webviews expose showModal without implementing it.
    }
  }
  dialog.setAttribute("open", "");
}

function hideDialog(dialog: HTMLDialogElement | null): void {
  if (dialog === null) {
    return;
  }
  if (dialog.open && typeof dialog.close === "function") {
    try {
      dialog.close();
      return;
    } catch {
      // Fall through to the attribute fallback used by older webviews.
    }
  }
  dialog.removeAttribute("open");
}

function clickedBackdrop(event: MouseEvent<HTMLDialogElement>): boolean {
  if (event.target !== event.currentTarget) {
    return false;
  }
  const bounds = event.currentTarget.getBoundingClientRect();
  return (
    event.clientX < bounds.left ||
    event.clientX > bounds.right ||
    event.clientY < bounds.top ||
    event.clientY > bounds.bottom
  );
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "An unexpected error occurred";
}

function requireAck(event: ServerEvent, operation: string): void {
  if (event.type === "ack" && event.ok) {
    return;
  }
  if (event.type === "ack") {
    throw new Error(event.error || `${operation} failed`);
  }
  throw new Error(`Expected acknowledgement, received ${event.type}`);
}

function formatPercent(ratio: number): number {
  return Math.round(Math.max(0, Math.min(1, ratio)) * 100);
}

interface Diagnostic {
  message: string;
  tone: DiagnosticTone;
}

/** Translate the latest gesture_test event into an actionable message.
 *
 * ``libraryNames`` resolves a confused-with gesture's id to its name (best
 * effort -- a generic fallback is used if the lookup has not resolved). */
function describeDiagnostic(
  event: GestureTestEvent | null,
  libraryNames: ReadonlyMap<number, string>,
): Diagnostic {
  if (event === null) {
    return { message: "Waiting for the camera…", tone: "info" };
  }
  switch (event.state) {
    case "no_hand":
      return {
        message: "No hand detected — bring your hand into view.",
        tone: "info",
      };
    case "moving":
      return { message: "Hold your hand still.", tone: "info" };
    case "holding":
      return { message: "Holding steady…", tone: "info" };
    case "attempt": {
      const confidencePct = formatPercent(event.confidence);
      if (event.is_target && event.fired) {
        return {
          message: event.action_description
            ? `Recognized (${confidencePct}%) — performed ${event.action_description}.`
            : `Recognized (${confidencePct}%).`,
          tone: "success",
        };
      }
      if (event.is_target) {
        return {
          message: `So close: matched ${confidencePct}% — needs ${formatPercent(
            event.min_confidence,
          )}%.`,
          tone: "warning",
        };
      }
      if (event.matched_gesture_id !== null) {
        const otherName =
          libraryNames.get(event.matched_gesture_id) ?? "another gesture";
        return {
          message: `That matched “${otherName}” instead (${confidencePct}%).`,
          tone: "warning",
        };
      }
      return {
        message: `Not recognized (best ${confidencePct}%).`,
        tone: "warning",
      };
    }
    default:
      return { message: "Waiting…", tone: "info" };
  }
}

export function GestureTestDialog({
  client,
  connectionState,
  gesture,
  returnFocusRef,
  libraryGestures = null,
  onClose,
  onReRecord,
}: GestureTestDialogProps) {
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [lastEvent, setLastEvent] = useState<GestureTestEvent | null>(null);
  const [passed, setPassed] = useState(false);
  const [failedAttempts, setFailedAttempts] = useState(0);
  const [actionBusy, setActionBusy] = useState<ActionBusy>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const dialogRef = useRef<HTMLDialogElement>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const previewUrlRef = useRef<string | null>(null);
  const mountedRef = useRef(true);
  const closingRef = useRef(false);
  // Freezes the diagnostic once passed so a later status tick (e.g. the
  // pose settling back to "holding" after it latches) cannot overwrite the
  // success message the Done button refers to.
  const passedRef = useRef(false);
  // Guards stop_gesture_test so it is sent exactly once, no matter which of
  // the several exit paths (pass, cancel, unmount, delete-and-re-record)
  // triggers it first. A leaked test mode would leave the system armed.
  const testStoppedRef = useRef(false);

  const connected = connectionState === "open";
  const isPoseMode = gesture.kind === "pose";
  const libraryNames = useMemo(
    () =>
      new Map((libraryGestures ?? []).map((entry) => [entry.id, entry.name])),
    [libraryGestures],
  );
  const diagnostic = useMemo(
    () => describeDiagnostic(lastEvent, libraryNames),
    [lastEvent, libraryNames],
  );

  const revokePreviewUrl = useCallback(() => {
    if (previewUrlRef.current !== null) {
      URL.revokeObjectURL(previewUrlRef.current);
      previewUrlRef.current = null;
    }
  }, []);

  const restoreTriggerFocus = useCallback(() => {
    queueMicrotask(() => returnFocusRef.current?.focus());
  }, [returnFocusRef]);

  const stopTest = useCallback(() => {
    if (testStoppedRef.current) {
      return;
    }
    testStoppedRef.current = true;
    client.send("stop_gesture_test");
  }, [client]);

  const close = useCallback(() => {
    if (closingRef.current) {
      return;
    }
    closingRef.current = true;
    stopTest();
    hideDialog(dialogRef.current);
    onClose();
    restoreTriggerFocus();
  }, [onClose, restoreTriggerFocus, stopTest]);

  // Start the test on mount, and guarantee stop_gesture_test on every exit
  // path (including an unexpected unmount) so a forgotten test can never
  // leave the daemon armed for real.
  useEffect(() => {
    testStoppedRef.current = false;
    client.send("start_gesture_test", { gesture_id: gesture.id });
    return () => {
      stopTest();
    };
  }, [client, gesture.id, stopTest]);

  useEffect(() => {
    mountedRef.current = true;
    showDialog(dialogRef.current);
    queueMicrotask(() => headingRef.current?.focus());
    return () => {
      mountedRef.current = false;
    };
  }, []);

  useEffect(() => {
    const unsubscribe = client.on("gesture_test", (event) => {
      if (event.type !== "gesture_test" || passedRef.current) {
        return;
      }
      setLastEvent(event);
      if (event.state !== "attempt") {
        return;
      }
      if (event.is_target && event.fired) {
        passedRef.current = true;
        setPassed(true);
      } else {
        setFailedAttempts((count) => count + 1);
      }
    });
    return unsubscribe;
  }, [client]);

  useEffect(() => {
    revokePreviewUrl();
    setPreviewUrl(null);
    if (!connected) {
      return;
    }
    let active = true;
    const unsubscribe = client.onPreviewFrame((frame) => {
      if (!active || typeof URL.createObjectURL !== "function") {
        return;
      }
      const nextUrl = URL.createObjectURL(frame);
      const previousUrl = previewUrlRef.current;
      previewUrlRef.current = nextUrl;
      setPreviewUrl(nextUrl);
      if (previousUrl !== null) {
        URL.revokeObjectURL(previousUrl);
      }
    });
    return () => {
      active = false;
      unsubscribe();
      revokePreviewUrl();
    };
  }, [client, connected, revokePreviewUrl]);

  const tryAgain = () => {
    setFailedAttempts(0);
    setLastEvent(null);
    setActionError(null);
  };

  const deleteAndReRecord = async () => {
    if (!connected || actionBusy !== null) {
      return;
    }
    setActionBusy("delete");
    setActionError(null);
    try {
      stopTest();
      const reply = await client.request("delete_gesture", {
        gesture_id: gesture.id,
      });
      requireAck(reply, "Delete");
      if (!mountedRef.current) {
        return;
      }
      closingRef.current = true;
      hideDialog(dialogRef.current);
      onReRecord(gesture);
    } catch (error) {
      if (mountedRef.current) {
        setActionError(errorMessage(error));
        setActionBusy(null);
      }
    }
  };

  const handleDialogKeyDown = (event: KeyboardEvent<HTMLDialogElement>) => {
    if (event.key === "Escape") {
      event.preventDefault();
      close();
    }
  };

  return (
    <dialog
      id="test-gesture-dialog"
      ref={dialogRef}
      className="confirm-dialog recording-dialog"
      aria-labelledby="test-gesture-title"
      aria-describedby="test-gesture-description"
      aria-modal="true"
      onCancel={(event) => {
        event.preventDefault();
        close();
      }}
      onClose={close}
      onKeyDown={handleDialogKeyDown}
      onClick={(event) => {
        if (clickedBackdrop(event)) {
          close();
        }
      }}
    >
      <div className="recording-dialog-content">
        <header className="recording-dialog-heading">
          <div>
            <h2 id="test-gesture-title" ref={headingRef} tabIndex={-1}>
              Test “{gesture.name}”
            </h2>
            <p id="test-gesture-description">
              Mapped to <strong>{gesture.actionDescription}</strong>. Perform
              the gesture so you can see it actually happen.
            </p>
          </div>
          <span className="status-badge">
            {passed ? "Passed" : "Testing"}
          </span>
        </header>

        <section className="recording-flow" aria-label="Test your gesture">
          <div
            className="camera-hero-frame recording-preview"
            data-live={previewUrl !== null}
          >
            {!connected ? (
              <div className="camera-hero-placeholder" role="status">
                Daemon disconnected — live preview unavailable.
              </div>
            ) : previewUrl === null ? (
              <div className="camera-hero-placeholder" role="status">
                Waiting for live camera preview…
              </div>
            ) : (
              <img
                src={previewUrl}
                alt="Live camera preview with hand-skeleton overlay while testing"
              />
            )}
            {previewUrl === null ? null : (
              <span className="live-badge">
                <span aria-hidden="true" />
                LIVE
              </span>
            )}
          </div>

          <p className="capture-cue">
            {isPoseMode ? "Hold your pose" : "Perform your gesture now"}
          </p>

          <p
            className="gesture-test-diagnostic"
            data-tone={passed ? "success" : diagnostic.tone}
            role="status"
            aria-live="polite"
          >
            {diagnostic.message}
          </p>

          {actionError === null ? null : <p role="alert">{actionError}</p>}

          {passed ? (
            <div className="dialog-actions">
              <button
                className="button button-primary"
                type="button"
                autoFocus
                onClick={close}
              >
                Done
              </button>
            </div>
          ) : (
            <>
              <div className="gesture-test-actions">
                <button
                  className="button button-secondary"
                  type="button"
                  disabled={actionBusy !== null}
                  onClick={tryAgain}
                >
                  Try again
                </button>
                <button
                  className="button button-secondary"
                  type="button"
                  disabled={!connected || actionBusy !== null}
                  onClick={() => void deleteAndReRecord()}
                >
                  {actionBusy === "delete"
                    ? "Deleting…"
                    : "Re-record this gesture"}
                </button>
                {failedAttempts >= SKIP_THRESHOLD ? (
                  <button
                    className="button button-secondary"
                    type="button"
                    disabled={actionBusy !== null}
                    onClick={close}
                  >
                    Skip for now — it may not work reliably
                  </button>
                ) : null}
              </div>

              <div className="dialog-actions">
                <button
                  className="button button-secondary"
                  type="button"
                  disabled={actionBusy !== null}
                  onClick={close}
                >
                  Close
                </button>
              </div>
            </>
          )}
        </section>
      </div>
    </dialog>
  );
}

export default GestureTestDialog;
