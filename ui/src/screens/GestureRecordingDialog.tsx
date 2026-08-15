import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent,
  type MouseEvent,
  type RefObject,
} from "react";

import type { GestureKind, RecordingEvent, ServerEvent } from "../lib/types";
import type { AirControlClient, ConnectionState } from "../lib/ws";

type RecordingClient = Pick<
  AirControlClient,
  "on" | "onPreviewFrame" | "onState" | "request" | "send"
>;

type RecordingStep = "setup" | "recording" | "refused";
type SetupError = { kind: "calibration" | "generic"; message: string };
type TakeDecision = "confirm_take" | "discard_take" | null;
/** Local, UI-driven view of the capture control while phase stays "recording". */
type CaptureView = "ready" | "countdown" | "capturing" | "pending_take";

const COUNTDOWN_START = 3;
const COUNTDOWN_TICK_MS = 1000;
/** Mirrors the daemon's default (aircontrol.recording.MAX_TAKE_SECONDS), used
 * only as a fallback before a "recording" event has reported the real value. */
const DEFAULT_MAX_TAKE_SECONDS = 10.0;

function isTextEntryTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) {
    return false;
  }
  if (target.isContentEditable) {
    return true;
  }
  const tag = target.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT";
}

function formatElapsed(seconds: number): string {
  return `${seconds.toFixed(1)}s`;
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

function takeRefusalMessage(reason: string | null | undefined): string {
  switch (reason) {
    case "pose unstable":
      return "Hold the pose steady — try that take again.";
    case "pose too similar to built-in":
      return "Too similar to a built-in pose — pick a more distinct shape.";
    default:
      return "No motion detected — try that take again.";
  }
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
  const [gestureKind, setGestureKind] = useState<GestureKind>("motion");
  const [recording, setRecording] = useState<RecordingEvent | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [startError, setStartError] = useState<SetupError | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [finishing, setFinishing] = useState(false);
  const [takeDecision, setTakeDecision] = useState<TakeDecision>(null);
  const [countdownValue, setCountdownValue] = useState<number | null>(null);
  const [awaitingCaptureStart, setAwaitingCaptureStart] = useState(false);
  const [awaitingCaptureStop, setAwaitingCaptureStop] = useState(false);
  const [elapsedSeconds, setElapsedSeconds] = useState(0);

  const dialogRef = useRef<HTMLDialogElement>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const nameInputRef = useRef<HTMLInputElement>(null);
  const pendingTakeHeadingRef = useRef<HTMLElement>(null);
  const recordTakeButtonRef = useRef<HTMLButtonElement>(null);
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
  const wasCapturingRef = useRef(false);
  const previousConnectionStateRef = useRef(connectionState);
  const fallbackModeRef = useRef(false);
  const fallbackInertElementsRef = useRef<
    Array<{ element: HTMLElement; wasInert: boolean }>
  >([]);

  const connected = connectionState === "open";
  const captureVisible = step === "recording";
  const pendingTake = recording?.phase === "pending_take";
  const isPoseMode = (recording?.gesture_kind ?? gestureKind) === "pose";
  const minimumMet =
    recording !== null && recording.takes_confirmed >= recording.min_takes;
  /** Every take the session will accept has been kept -- there is nothing
   * left to capture, so the record control is retired in favour of Save. */
  const allTakesCaptured =
    recording !== null && recording.takes_confirmed >= recording.max_takes;

  const phaseView: CaptureView =
    pendingTake && recording !== null
      ? "pending_take"
      : countdownValue !== null
        ? "countdown"
        : awaitingCaptureStart || recording?.capture_state === "capturing"
          ? "capturing"
          : "ready";

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

  /** A recording just saved: close the dialog and hand off to the caller
   * immediately, exactly as before the (now removed) mandatory in-dialog
   * test step -- the caller (Gestures.tsx) routes the saved gesture into
   * mapping, and once it is mapped opens GestureTestDialog for it. */
  const completeSave = useCallback(
    (event: RecordingEvent) => {
      if (terminalHandledRef.current) {
        return;
      }
      terminalHandledRef.current = true;
      acceptRecordingEventsRef.current = false;
      const gestureId = event.outcome?.gesture_id ?? null;
      const name = event.name || gestureName.trim();
      shouldCancelOnUnmountRef.current = false;
      closingRef.current = true;
      restoreFallbackModal();
      hideDialog(dialogRef.current);
      onSaved({ id: gestureId, name });
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
        setRecording(event);
        completeSave(event);
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
    [completeSave],
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
      setCountdownValue(null);
      setAwaitingCaptureStart(false);
      setAwaitingCaptureStop(false);
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

  // Leaving the recording step (dismissed, saved, or refused) drops any
  // in-flight local countdown/timer so re-entry starts clean.
  useEffect(() => {
    if (step !== "recording") {
      setCountdownValue(null);
      setAwaitingCaptureStart(false);
      setAwaitingCaptureStop(false);
      setElapsedSeconds(0);
    }
  }, [step]);

  // Once the daemon confirms the capture window is open or already closed
  // (pending take, or back to idle on a no-motion refusal), the optimistic
  // "sent, waiting" flags are no longer needed.
  useEffect(() => {
    const captureState = recording?.capture_state;
    if (captureState === "capturing" || captureState === "pending_take") {
      setAwaitingCaptureStart(false);
    }
    if (captureState !== "capturing") {
      setAwaitingCaptureStop(false);
    }
  }, [recording?.capture_state]);

  // Local 3-2-1 countdown: purely client-side, gives the user time to raise
  // their hand into frame, then ends by sending start_take. There is no
  // countdown on stop -- end_take is sent immediately.
  useEffect(() => {
    if (countdownValue === null) {
      return;
    }
    const timer = setTimeout(() => {
      if (countdownValue <= 1) {
        setCountdownValue(null);
        if (connected) {
          setAwaitingCaptureStart(true);
          client.send("start_take");
        }
        return;
      }
      setCountdownValue(countdownValue - 1);
    }, COUNTDOWN_TICK_MS);
    return () => clearTimeout(timer);
  }, [countdownValue, connected, client]);

  // Elapsed-time stopwatch: there is no fixed window any more, so the
  // primary readout while capturing is how long the user has been
  // performing the gesture, counted up locally from the optimistic
  // "capturing" transition (countdown end or the daemon's own event,
  // whichever comes first). A plain interval (rather than
  // requestAnimationFrame) keeps this simple to drive under fake timers.
  useEffect(() => {
    const isCapturing = phaseView === "capturing";
    if (!isCapturing) {
      wasCapturingRef.current = false;
      setElapsedSeconds(0);
      return;
    }
    if (wasCapturingRef.current) {
      return;
    }
    wasCapturingRef.current = true;
    const startedAt = performance.now();
    const interval = setInterval(() => {
      setElapsedSeconds((performance.now() - startedAt) / 1000);
    }, 100);
    return () => clearInterval(interval);
  }, [phaseView]);

  const toggleCapture = useCallback(() => {
    if (!connected || recording === null) {
      return;
    }
    if (countdownValue !== null) {
      return;
    }
    if (phaseView === "ready") {
      if (recording.takes_confirmed >= recording.max_takes) {
        return;
      }
      setActionError(null);
      setCountdownValue(COUNTDOWN_START);
      return;
    }
    if (phaseView === "capturing" && !awaitingCaptureStop) {
      setAwaitingCaptureStop(true);
      client.send("end_take");
    }
  }, [awaitingCaptureStop, client, connected, countdownValue, phaseView, recording]);

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
        gesture_kind: gestureKind,
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

  const handleDialogKeyDown = (event: KeyboardEvent<HTMLDialogElement>) => {
    if (
      step === "recording" &&
      event.key === " " &&
      !isTextEntryTarget(event.target) &&
      !(event.target instanceof HTMLButtonElement)
    ) {
      event.preventDefault();
      toggleCapture();
    }

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

  const maxTakeSeconds = recording?.max_take_seconds ?? DEFAULT_MAX_TAKE_SECONDS;
  const takeButtonLabel = useMemo(() => {
    if (phaseView === "capturing") {
      return "Stop & capture";
    }
    return "Record take";
  }, [phaseView]);

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
      onKeyDown={handleDialogKeyDown}
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

            <fieldset className="gesture-kind-fieldset">
              <legend>Gesture type</legend>
              <div className="segmented-control">
                <label
                  className="segmented-option"
                  data-selected={gestureKind === "motion"}
                >
                  <input
                    type="radio"
                    name="gesture-kind"
                    value="motion"
                    checked={gestureKind === "motion"}
                    disabled={starting}
                    onChange={() => setGestureKind("motion")}
                  />
                  Motion gesture
                </label>
                <label
                  className="segmented-option"
                  data-selected={gestureKind === "pose"}
                >
                  <input
                    type="radio"
                    name="gesture-kind"
                    value="pose"
                    checked={gestureKind === "pose"}
                    disabled={starting}
                    onChange={() => setGestureKind("pose")}
                  />
                  Hand pose
                </label>
              </div>
              <p>
                {gestureKind === "pose"
                  ? "Record a held hand shape rather than a moving gesture."
                  : "Record a deliberate movement, from start to finish."}
              </p>
            </fieldset>

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

            {recording === null ? null : (
              <div className="recording-progress" role="status" aria-live="polite">
                <div className="recording-progress-heading">
                  <span>Takes captured</span>
                  <strong>
                    {recording.takes_confirmed} of {recording.max_takes}
                  </strong>
                </div>
                <p className="recording-progress-hint">
                  {allTakesCaptured
                    ? `All ${recording.max_takes} captured.`
                    : minimumMet
                      ? `Ready to save. You can add up to ${recording.max_takes} for better reliability.`
                      : `Capture at least ${recording.min_takes}.`}
                </p>
                <progress
                  aria-label={`${recording.takes_confirmed} of ${recording.max_takes} takes captured`}
                  aria-valuetext={`${recording.takes_confirmed} of ${recording.max_takes} takes captured`}
                  value={recording.takes_confirmed}
                  max={Math.max(recording.max_takes, 1)}
                />
                <p className="recording-progress-variation-hint">
                  Vary each take slightly — a small change in angle, distance
                  or hand position — so it still recognises you in real use.
                </p>
              </div>
            )}

            {recording !== null &&
            phaseView !== "pending_take" &&
            !(phaseView === "ready" && allTakesCaptured) ? (
              <div className="capture-control">
                {phaseView === "countdown" ? (
                  <p
                    className="capture-countdown"
                    role="status"
                    aria-live="assertive"
                  >
                    {countdownValue}
                  </p>
                ) : null}

                {phaseView === "capturing" ? (
                  <div className="capture-active">
                    <p
                      className="capture-cue"
                      role="status"
                      aria-live="assertive"
                    >
                      {isPoseMode
                        ? ">>> HOLD THE POSE STEADY <<<"
                        : ">>> PERFORM NOW <<<"}
                    </p>
                    <p className="capture-stop-hint">
                      {isPoseMode
                        ? "Press stop once you have held the pose steady"
                        : "Press stop when you finish the movement"}
                    </p>
                    {isPoseMode ? (
                      <div
                        className="steadiness-meter"
                        data-steady={recording.pose_steady === true}
                        role="status"
                        aria-live="polite"
                      >
                        <span className="steadiness-meter-track" aria-hidden="true">
                          <span className="steadiness-meter-fill" />
                        </span>
                        <span className="steadiness-meter-label">
                          {recording.pose_steady === true
                            ? "Steady"
                            : recording.pose_steady === false
                              ? "Hold still…"
                              : "Checking…"}
                        </span>
                      </div>
                    ) : null}
                    <p className="capture-elapsed" role="status" aria-live="off">
                      {formatElapsed(elapsedSeconds)}
                    </p>
                    <p className="capture-elapsed-hint">
                      Auto-stops at {Math.round(maxTakeSeconds)}s if you forget to
                      press stop — stopping earlier works just as well.
                    </p>
                  </div>
                ) : null}

                {phaseView === "ready" && recording.last_take_refused ? (
                  <p className="capture-refusal" role="status">
                    {takeRefusalMessage(recording.last_take_refused)}
                  </p>
                ) : null}

                {phaseView === "ready" || phaseView === "capturing" ? (
                  <button
                    ref={recordTakeButtonRef}
                    className="button button-primary capture-trigger"
                    type="button"
                    disabled={
                      !connected ||
                      (phaseView === "capturing" && awaitingCaptureStop)
                    }
                    onClick={toggleCapture}
                  >
                    {takeButtonLabel}
                  </button>
                ) : null}
              </div>
            ) : null}

            {recording !== null && phaseView === "ready" && allTakesCaptured ? (
              <div className="state-panel recording-complete" role="status">
                <strong>All {recording.max_takes} takes captured</strong>
                <span>You're ready to save this gesture.</span>
              </div>
            ) : null}

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
