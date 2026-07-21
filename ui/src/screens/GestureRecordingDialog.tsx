import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent,
  type MouseEvent,
  type RefObject,
} from "react";

import type {
  RecordingCaptureState,
  RecordingEvent,
  ServerEvent,
} from "../lib/types";
import type { AirControlClient, ConnectionState } from "../lib/ws";

type RecordingClient = Pick<
  AirControlClient,
  "on" | "onPreviewFrame" | "onState" | "request" | "send"
>;

type RecordingStep = "setup" | "recording" | "refused";
type SetupError = { kind: "calibration" | "generic"; message: string };
type TakeDecision = "confirm_take" | "discard_take" | null;

/** Human-readable live status line for the recording step's capture_state. */
function captureStateMessage(
  captureState: RecordingCaptureState | undefined,
): string {
  switch (captureState) {
    case "searching":
      return "Looking for your hand…";
    case "hand_present":
      return "Hand detected — perform the gesture";
    case "in_motion":
      return "Motion detected — pause to capture";
    case "pending_take":
      return "Take captured — keep or discard";
    case "idle":
    default:
      return "Getting ready…";
  }
}

/** Identifies the gesture a completed recording produced, for the caller to map next. */
export interface SavedGesture {
  id: number | null;
  name: string;
}

interface GestureRecordingDialogProps {
  client: RecordingClient;
  connectionState: ConnectionState;
  returnFocusRef: RefObject<HTMLButtonElement>;
  onDismiss: () => void;
  onSaved: (gesture: SavedGesture) => void;
  onNavigateCalibration: () => void;
}

function showDialog(
  dialog: HTMLDialogElement | null,
  fallbackAlreadyActive: boolean,
): boolean {
  if (dialog === null) {
    return false;
  }
  if (dialog.open) {
    return !fallbackAlreadyActive;
  }
  if (typeof dialog.showModal === "function") {
    try {
      dialog.showModal();
      return true;
    } catch {
      // Some embedded webviews expose showModal without implementing it.
    }
  }
  dialog.setAttribute("open", "");
  return false;
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

function startFailure(event: ServerEvent): SetupError | null {
  if (event.type !== "ack") {
    return {
      kind: "generic",
      message: "The daemon returned an unexpected recording response. Try again.",
    };
  }
  if (event.ok) {
    return null;
  }
  if (event.error === "calibrate first") {
    return {
      kind: "calibration",
      message: "Run calibration first from the Calibration screen.",
    };
  }
  if (event.error === "no store") {
    return {
      kind: "generic",
      message:
        "Recording is unavailable because the local gesture library could not be opened.",
    };
  }
  return {
    kind: "generic",
    message: event.error
      ? `Recording could not start: ${event.error}.`
      : "Recording could not start. Try again.",
  };
}

function refusalMessage(event: RecordingEvent): string {
  const outcome = event.outcome;
  switch (outcome?.reason) {
    case "inconsistent":
      return "Your takes were too inconsistent — try again.";
    case "too similar":
      return outcome.conflict_gesture_name === null
        ? "Too similar to an existing gesture — make this motion more distinct."
        : `Too similar to ${outcome.conflict_gesture_name} — make this motion more distinct.`;
    case "resembles desk motion":
      return "This looks like normal desk motion — make it more distinct.";
    case "need more takes":
      return "Record a few more takes before saving.";
    default:
      return "The gesture could not be saved. Try recording it again.";
  }
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

const FALLBACK_FOCUSABLE = [
  "button:not(:disabled)",
  "input:not(:disabled)",
  "select:not(:disabled)",
  "textarea:not(:disabled)",
  "a[href]",
  "[tabindex]:not([tabindex='-1'])",
].join(",");

export function GestureRecordingDialog({
  client,
  connectionState,
  returnFocusRef,
  onDismiss,
  onSaved,
  onNavigateCalibration,
}: GestureRecordingDialogProps) {
  const [step, setStep] = useState<RecordingStep>("setup");
  const [gestureName, setGestureName] = useState("");
  const [recording, setRecording] = useState<RecordingEvent | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [startError, setStartError] = useState<SetupError | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [finishing, setFinishing] = useState(false);
  const [takeDecision, setTakeDecision] = useState<TakeDecision>(null);

  const dialogRef = useRef<HTMLDialogElement>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const nameInputRef = useRef<HTMLInputElement>(null);
  const pendingTakeHeadingRef = useRef<HTMLElement>(null);
  const previewUrlRef = useRef<string | null>(null);
  const mountedRef = useRef(true);
  const shouldCancelOnUnmountRef = useRef(true);
  const cancelQueuedRef = useRef(false);
  const closingRef = useRef(false);
  const acceptRecordingEventsRef = useRef(false);
  const startMayBeActiveRef = useRef(false);
  const terminalHandledRef = useRef(false);
  const lifecycleGenerationRef = useRef(0);
  const previousStepRef = useRef<RecordingStep>("setup");
  const previousPhaseRef = useRef<RecordingEvent["phase"] | null>(null);
  const previousConnectionStateRef = useRef(connectionState);
  const fallbackModeRef = useRef(false);
  const fallbackInertElementsRef = useRef<
    Array<{ element: HTMLElement; wasInert: boolean }>
  >([]);

  const connected = connectionState === "open";
  const captureVisible = step === "recording";

  const revokePreviewUrl = useCallback(() => {
    if (previewUrlRef.current !== null) {
      URL.revokeObjectURL(previewUrlRef.current);
      previewUrlRef.current = null;
    }
  }, []);

  const restoreTriggerFocus = useCallback(() => {
    queueMicrotask(() => returnFocusRef.current?.focus());
  }, [returnFocusRef]);

  const restoreFallbackModal = useCallback(() => {
    for (const { element, wasInert } of fallbackInertElementsRef.current) {
      if (!wasInert) {
        element.removeAttribute("inert");
      }
    }
    fallbackInertElementsRef.current = [];
  }, []);

  const activateFallbackModal = useCallback(() => {
    const dialog = dialogRef.current;
    if (dialog === null) {
      return;
    }
    const elements = new Set<HTMLElement>();
    let branch: HTMLElement = dialog;
    while (branch.parentElement !== null) {
      const parent = branch.parentElement;
      for (const sibling of Array.from(parent.children)) {
        if (sibling !== branch && sibling instanceof HTMLElement) {
          elements.add(sibling);
        }
      }
      if (parent === document.body) {
        break;
      }
      branch = parent;
    }
    fallbackInertElementsRef.current = Array.from(elements, (element) => {
      const wasInert = element.hasAttribute("inert");
      element.setAttribute("inert", "");
      return { element, wasInert };
    });
  }, []);

  const queueRecordingCancel = useCallback(() => {
    if (cancelQueuedRef.current) {
      return;
    }
    cancelQueuedRef.current = true;
    shouldCancelOnUnmountRef.current = false;

    let unsubscribe: (() => void) | null = null;
    let sent = false;
    const sendWhenOpen = (state: ConnectionState) => {
      if (state !== "open" || sent) {
        return;
      }
      sent = true;
      client.send("cancel_recording");
      unsubscribe?.();
    };
    unsubscribe = client.onState(sendWhenOpen);
    if (sent) {
      unsubscribe();
    }
  }, [client]);

  const dismiss = useCallback(
    (cancelRecording: boolean, restoreFocus: boolean) => {
      if (closingRef.current) {
        return;
      }
      closingRef.current = true;
      if (cancelRecording && shouldCancelOnUnmountRef.current) {
        queueRecordingCancel();
      }
      shouldCancelOnUnmountRef.current = false;
      restoreFallbackModal();
      hideDialog(dialogRef.current);
      onDismiss();
      if (restoreFocus) {
        restoreTriggerFocus();
      }
    },
    [onDismiss, queueRecordingCancel, restoreFallbackModal, restoreTriggerFocus],
  );

  const completeSavedRecording = useCallback(
    (event: RecordingEvent) => {
      if (terminalHandledRef.current) {
        return;
      }
      terminalHandledRef.current = true;
      acceptRecordingEventsRef.current = false;
      shouldCancelOnUnmountRef.current = false;
      closingRef.current = true;
      restoreFallbackModal();
      hideDialog(dialogRef.current);
      onSaved({
        id: event.outcome?.gesture_id ?? null,
        name: event.name || gestureName.trim(),
      });
      restoreTriggerFocus();
    },
    [gestureName, onSaved, restoreFallbackModal, restoreTriggerFocus],
  );

  useEffect(() => {
    const dialog = dialogRef.current;
    const modal = showDialog(dialog, fallbackModeRef.current);
    fallbackModeRef.current = !modal;
    if (!modal) {
      dialog?.setAttribute("data-fallback-modal", "true");
      activateFallbackModal();
    }
    queueMicrotask(() => nameInputRef.current?.focus());
    return () => {
      dialog?.removeAttribute("data-fallback-modal");
      restoreFallbackModal();
    };
  }, [activateFallbackModal, restoreFallbackModal]);

  useEffect(() => {
    if (!fallbackModeRef.current) {
      return;
    }
    const closeFromFallbackBackdrop = (event: PointerEvent) => {
      const dialog = dialogRef.current;
      if (
        dialog !== null &&
        event.target instanceof Node &&
        !dialog.contains(event.target)
      ) {
        event.preventDefault();
        dismiss(true, true);
      }
    };
    document.addEventListener("pointerdown", closeFromFallbackBackdrop, true);
    return () =>
      document.removeEventListener(
        "pointerdown",
        closeFromFallbackBackdrop,
        true,
      );
  }, [dismiss]);

  useEffect(() => {
    const generation = lifecycleGenerationRef.current + 1;
    lifecycleGenerationRef.current = generation;
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      queueMicrotask(() => {
        const trulyUnmounted =
          lifecycleGenerationRef.current === generation && !mountedRef.current;
        if (trulyUnmounted && shouldCancelOnUnmountRef.current) {
          queueRecordingCancel();
        }
      });
    };
  }, [queueRecordingCancel]);

  const handleRecordingEvent = useCallback(
    (event: RecordingEvent, authoritative = false) => {
      if (!authoritative && !acceptRecordingEventsRef.current) {
        return;
      }

      if (event.name.trim() !== "") {
        setGestureName(event.name);
      }
      setTakeDecision(null);
      setActionError(null);

      if (event.phase === "saved") {
        completeSavedRecording(event);
        return;
      }
      if (event.phase === "refused") {
        acceptRecordingEventsRef.current = false;
        setRecording(event);
        setFinishing(false);
        setStep("refused");
        return;
      }
      if (event.phase === "capturing" || event.phase === "pending_take") {
        acceptRecordingEventsRef.current = true;
        setRecording(event);
        setFinishing(false);
        setStep("recording");
        return;
      }

      acceptRecordingEventsRef.current = false;
      setRecording(null);
      setFinishing(false);
      setStep("setup");
      setStartError({
        kind: "generic",
        message: "Recording ended before the gesture was saved.",
      });
    },
    [completeSavedRecording],
  );

  useEffect(() => {
    const unsubscribe = client.on("recording", (event) => {
      if (event.type === "recording") {
        handleRecordingEvent(event);
      }
    });
    return unsubscribe;
  }, [client, handleRecordingEvent]);

  useEffect(() => {
    revokePreviewUrl();
    setPreviewUrl(null);
    if (!captureVisible || !connected) {
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
  }, [captureVisible, client, connected, revokePreviewUrl]);

  const reconcileRecordingState = useCallback(async (): Promise<boolean> => {
    try {
      const event = await client.request("get_recording_state");
      if (!mountedRef.current) {
        return false;
      }
      if (event.type !== "recording") {
        throw new Error(`Expected recording state, received ${event.type}`);
      }
      handleRecordingEvent(event, true);
      return true;
    } catch (error) {
      if (mountedRef.current) {
        setTakeDecision(null);
        setFinishing(false);
        setActionError(`Could not refresh recording state: ${errorMessage(error)}`);
      }
      return false;
    }
  }, [client, handleRecordingEvent]);

  useEffect(() => {
    const previousState = previousConnectionStateRef.current;
    previousConnectionStateRef.current = connectionState;
    if (!connected) {
      setTakeDecision(null);
      setFinishing(false);
      if (startMayBeActiveRef.current) {
        setStarting(false);
        setStartError({
          kind: "generic",
          message:
            "The connection was lost while recording started. Reconnect to verify its state, or Cancel safely.",
        });
      }
      return;
    }
    if (previousState !== "open" && startMayBeActiveRef.current) {
      setStarting(true);
      setStartError(null);
      void reconcileRecordingState().then((reconciled) => {
        if (!mountedRef.current) {
          return;
        }
        setStarting(false);
        if (reconciled) {
          startMayBeActiveRef.current = false;
        } else {
          setStartError({
            kind: "generic",
            message:
              "The connection returned, but recording state could not be verified. Cancel and try again.",
          });
        }
      });
    } else if (
      previousState !== "open" &&
      acceptRecordingEventsRef.current
    ) {
      void reconcileRecordingState();
    }
  }, [connected, connectionState, reconcileRecordingState]);

  useEffect(() => {
    const previousStep = previousStepRef.current;
    previousStepRef.current = step;
    if (previousStep === step) {
      return;
    }
    queueMicrotask(() => {
      if (step === "setup") {
        nameInputRef.current?.focus();
      } else {
        headingRef.current?.focus();
      }
    });
  }, [step]);

  useEffect(() => {
    const phase = recording?.phase ?? null;
    const previousPhase = previousPhaseRef.current;
    previousPhaseRef.current = phase;
    if (phase === "pending_take" && previousPhase !== "pending_take") {
      queueMicrotask(() => pendingTakeHeadingRef.current?.focus());
    } else if (
      previousPhase === "pending_take" &&
      phase === "capturing"
    ) {
      queueMicrotask(() => headingRef.current?.focus());
    }
  }, [recording?.phase]);

  const submitStart = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const name = gestureName.trim();
    if (!connected || name === "" || starting || startMayBeActiveRef.current) {
      return;
    }

    setStarting(true);
    setStartError(null);
    setActionError(null);
    setRecording(null);
    acceptRecordingEventsRef.current = false;
    startMayBeActiveRef.current = true;
    try {
      const reply = await client.request("start_recording", {
        gesture_name: name,
      });
      if (!mountedRef.current) {
        return;
      }
      const failure = startFailure(reply);
      if (failure !== null) {
        acceptRecordingEventsRef.current = false;
        startMayBeActiveRef.current = false;
        setRecording(null);
        setTakeDecision(null);
        setFinishing(false);
        setStep("setup");
        setStartError(failure);
        return;
      }
      acceptRecordingEventsRef.current = true;
      startMayBeActiveRef.current = false;
      setGestureName(name);
      setStep("recording");
      await reconcileRecordingState();
    } catch (error) {
      if (mountedRef.current) {
        acceptRecordingEventsRef.current = false;
        startMayBeActiveRef.current = false;
        setRecording(null);
        setTakeDecision(null);
        setFinishing(false);
        setStep("setup");
        setStartError({
          kind: "generic",
          message: `Recording could not start: ${errorMessage(error)}.`,
        });
      }
    } finally {
      if (mountedRef.current) {
        setStarting(false);
      }
    }
  };

  const decideTake = (command: Exclude<TakeDecision, null>) => {
    if (!connected || takeDecision !== null) {
      return;
    }
    setTakeDecision(command);
    client.send(command);
    void reconcileRecordingState();
  };

  const finishRecording = async () => {
    if (!connected || recording === null || finishing) {
      return;
    }
    if (recording.takes_confirmed < recording.min_takes) {
      return;
    }

    setFinishing(true);
    setActionError(null);
    try {
      const reply = await client.request("finish_recording");
      if (mountedRef.current) {
        requireAck(reply, "Saving the gesture");
        await reconcileRecordingState();
      }
    } catch (error) {
      if (mountedRef.current) {
        setFinishing(false);
        setActionError(errorMessage(error));
      }
    }
  };

  const tryAgain = () => {
    if (!connected) {
      return;
    }
    client.send("cancel_recording");
    acceptRecordingEventsRef.current = false;
    terminalHandledRef.current = false;
    setStep("setup");
    setRecording(null);
    setStartError(null);
    setActionError(null);
    setStarting(false);
    setFinishing(false);
    setTakeDecision(null);
  };

  const goToCalibration = () => {
    dismiss(true, false);
    queueMicrotask(onNavigateCalibration);
  };

  const handleFallbackKeyDown = (event: KeyboardEvent<HTMLDialogElement>) => {
    if (!fallbackModeRef.current) {
      return;
    }
    if (event.key === "Escape") {
      event.preventDefault();
      dismiss(true, true);
      return;
    }
    if (event.key !== "Tab") {
      return;
    }

    const focusable = Array.from(
      event.currentTarget.querySelectorAll<HTMLElement>(FALLBACK_FOCUSABLE),
    );
    if (focusable.length === 0) {
      event.preventDefault();
      headingRef.current?.focus();
      return;
    }
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    const active = document.activeElement;
    const activeIndex =
      active instanceof HTMLElement ? focusable.indexOf(active) : -1;
    if (!event.currentTarget.contains(active) || activeIndex === -1) {
      event.preventDefault();
      (event.shiftKey ? last : first).focus();
    } else if (event.shiftKey && active === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && active === last) {
      event.preventDefault();
      first.focus();
    }
  };

  const minimumMet =
    recording !== null && recording.takes_confirmed >= recording.min_takes;
  const pendingTake = recording?.phase === "pending_take";

  return (
    <dialog
      id="record-gesture-dialog"
      ref={dialogRef}
      className="confirm-dialog recording-dialog"
      aria-labelledby="record-gesture-title"
      aria-describedby="record-gesture-description"
      aria-modal="true"
      onCancel={(event) => {
        event.preventDefault();
        dismiss(true, true);
      }}
      onClose={() => dismiss(true, true)}
      onKeyDown={handleFallbackKeyDown}
      onClick={(event) => {
        if (clickedBackdrop(event)) {
          dismiss(true, true);
        }
      }}
    >
      <div className="recording-dialog-content">
        <header className="recording-dialog-heading">
          <div>
            <h2 id="record-gesture-title" ref={headingRef} tabIndex={-1}>
              Add Gesture
            </h2>
            <p id="record-gesture-description">
              Record several deliberate examples so AirControl can recognize the
              motion reliably.
            </p>
          </div>
          <span className="status-badge">
            {step === "setup"
              ? "Set up"
              : step === "refused"
                ? "Not saved"
                : pendingTake
                  ? "Review take"
                  : "Recording"}
          </span>
        </header>

        {step === "setup" ? (
          <form className="recording-flow" onSubmit={(event) => void submitStart(event)}>
            <div className="setting-field">
              <label htmlFor="record-gesture-name">Gesture name</label>
              <input
                id="record-gesture-name"
                ref={nameInputRef}
                autoFocus
                autoComplete="off"
                value={gestureName}
                disabled={starting}
                placeholder="For example, Window circle"
                onChange={(event) => setGestureName(event.target.value)}
              />
              <p>Use a short name that describes the motion, not its mapped action.</p>
            </div>

            {!connected ? (
              <div className="state-panel state-panel-offline" role="status">
                <strong>Daemon not connected</strong>
                <span>Start AirControl with app.cmd, then try again.</span>
              </div>
            ) : null}

            {startError === null ? null : (
              <div
                className={`state-panel${
                  startError.kind === "generic" ? " state-panel-error" : ""
                }`}
                role="alert"
              >
                <strong>
                  {startError.kind === "calibration"
                    ? "Calibration required"
                    : "Could not start recording"}
                </strong>
                <span>{startError.message}</span>
                {startError.kind === "calibration" ? (
                  <button
                    className="button button-secondary"
                    type="button"
                    onClick={goToCalibration}
                  >
                    Go to Calibration
                  </button>
                ) : null}
              </div>
            )}

            <div className="dialog-actions">
              <button
                className="button button-secondary"
                type="button"
                onClick={() => dismiss(true, true)}
              >
                Cancel
              </button>
              <button
                className="button button-primary"
                type="submit"
                disabled={
                  !connected ||
                  starting ||
                  startMayBeActiveRef.current ||
                  gestureName.trim() === ""
                }
              >
                {starting
                  ? "Starting…"
                  : startMayBeActiveRef.current
                    ? "Verification required"
                    : "Start recording"}
              </button>
            </div>
          </form>
        ) : null}

        {step === "recording" ? (
          <section className="recording-flow" aria-label="Gesture capture">
            <div className="recording-status-row">
              <strong className="recording-name">
                {recording?.name || gestureName}
              </strong>
              {recording === null ? (
                <span className="inline-status" role="status">
                  Waiting for recording status…
                </span>
              ) : null}
            </div>

            {recording === null ? null : (
              <p
                className="recording-capture-status"
                role="status"
                aria-live="polite"
              >
                {captureStateMessage(recording.capture_state)}
              </p>
            )}

            <div className="camera-hero-frame recording-preview" data-live={previewUrl !== null}>
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
                  alt="Live camera preview with hand-skeleton overlay while recording"
                />
              )}
              {previewUrl === null ? null : (
                <span className="live-badge">
                  <span aria-hidden="true" />
                  LIVE
                </span>
              )}
            </div>

            {recording === null ? (
              <p>Perform the gesture, then pause. Take limits will appear here.</p>
            ) : (
              <div className="recording-progress" role="status" aria-live="polite">
                <div className="recording-progress-heading">
                  <span>Confirmed takes</span>
                  <strong>
                    {recording.takes_confirmed} / {recording.min_takes} minimum
                  </strong>
                </div>
                <progress
                  aria-label={`${recording.takes_confirmed} of ${recording.min_takes} minimum takes confirmed`}
                  aria-valuetext={`${recording.takes_confirmed} confirmed; minimum ${recording.min_takes}${
                    minimumMet ? " met" : " not met"
                  }; maximum ${recording.max_takes}`}
                  value={Math.min(recording.takes_confirmed, recording.min_takes)}
                  max={Math.max(recording.min_takes, 1)}
                />
                <p>
                  Perform the gesture, then pause. Keep {recording.takes_confirmed} of{" "}
                  {recording.min_takes}–{recording.max_takes} takes.
                </p>
              </div>
            )}

            {pendingTake && recording !== null ? (
              <div className="state-panel recording-take-review" role="status">
                <div className="recording-take-copy">
                  <strong ref={pendingTakeHeadingRef} tabIndex={-1}>
                    Take captured
                    {recording.pending_take_frames === null
                      ? ""
                      : ` (${recording.pending_take_frames} frames)`}
                  </strong>
                  <span>Keep clear examples and discard accidental motion.</span>
                </div>
                <div className="recording-take-actions">
                  <button
                    className="button button-secondary"
                    type="button"
                    disabled={!connected || takeDecision !== null}
                    onClick={() => decideTake("discard_take")}
                  >
                    Discard
                  </button>
                  <button
                    className="button button-primary"
                    type="button"
                    disabled={!connected || takeDecision !== null}
                    onClick={() => decideTake("confirm_take")}
                  >
                    Keep
                  </button>
                </div>
              </div>
            ) : null}

            {takeDecision === null ? null : (
              <span className="inline-status" role="status" aria-live="polite">
                {takeDecision === "confirm_take"
                  ? "Keeping take…"
                  : "Discarding take…"}
              </span>
            )}

            {actionError === null ? null : <p role="alert">{actionError}</p>}

            <div className="dialog-actions">
              <button
                className="button button-secondary"
                type="button"
                onClick={() => dismiss(true, true)}
              >
                Cancel
              </button>
              <button
                className="button button-primary"
                type="button"
                disabled={
                  !connected ||
                  !minimumMet ||
                  finishing
                }
                onClick={() => void finishRecording()}
              >
                {finishing ? "Evaluating…" : "Save gesture"}
              </button>
            </div>
          </section>
        ) : null}

        {step === "refused" && recording !== null ? (
          <section className="recording-flow" aria-labelledby="recording-refused-title">
            <div>
              <h3 id="recording-refused-title">Gesture wasn’t saved</h3>
              <p role="alert">{refusalMessage(recording)}</p>
            </div>
            <div className="dialog-actions recording-error-actions">
              <button
                className="button button-secondary"
                type="button"
                onClick={() => dismiss(true, true)}
              >
                Cancel
              </button>
              <button
                className="button button-primary"
                type="button"
                disabled={!connected}
                onClick={tryAgain}
              >
                Try again
              </button>
            </div>
          </section>
        ) : null}
      </div>
    </dialog>
  );
}

export default GestureRecordingDialog;
