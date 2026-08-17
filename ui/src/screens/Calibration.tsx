import { useCallback, useEffect, useRef, useState } from "react";

import type {
  CalibrationEvent,
  CalibrationStep,
  LibraryGesture,
  ServerEvent,
  SettingsEvent,
} from "../lib/types";
import {
  DaemonNotice,
  ErrorState,
  LoadingState,
  ScreenHeader,
} from "../lib/ui";
import type { AirControlClient, ConnectionState } from "../lib/ws";
import { AdvancedTuning } from "./AdvancedTuning";

type ScreenClient = Pick<
  AirControlClient,
  "request" | "send" | "on" | "onPreviewFrame"
>;

export interface CalibrationProps {
  client: ScreenClient;
  connectionState: ConnectionState;
}

interface CalibrationContext {
  effectiveSettings: SettingsEvent["payload"];
  gestures: LibraryGesture[];
}

const STEP_TITLES: Record<CalibrationStep, string> = {
  "": "",
  framing: "Frame your interaction area",
  hand_snapshot: "Hand size snapshot",
  motion_signature: "Motion signature",
  negative_capture: "Normal work capture",
  lighting: "Lighting check",
  complete: "Calibration complete",
};

const STEP_ORDER: CalibrationStep[] = [
  "framing",
  "hand_snapshot",
  "motion_signature",
  "negative_capture",
  "lighting",
];

/** Steps that sample automatically and finish on their own; the user waits
 * rather than presses Continue (mirrors the CLI, where a stray Space here is
 * a harmless no-op). */
const AUTO_ADVANCE_STEPS = new Set<CalibrationStep>([
  "hand_snapshot",
  "negative_capture",
]);

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Unknown calibration error";
}

function requireSettings(event: ServerEvent): SettingsEvent["payload"] {
  if (event.type === "settings") {
    return event.payload;
  }
  if (event.type === "ack" && !event.ok) {
    throw new Error(event.error || "Daemon settings are unavailable");
  }
  throw new Error(`Expected settings, received ${event.type}`);
}

function requireLibrary(event: ServerEvent): LibraryGesture[] {
  if (event.type === "library") {
    return event.gestures;
  }
  if (event.type === "ack" && !event.ok) {
    throw new Error(event.error || "Gesture library is unavailable");
  }
  throw new Error(`Expected library, received ${event.type}`);
}

function reportedValue(value: unknown): string {
  if (typeof value === "string" || typeof value === "number") {
    return String(value);
  }
  return "Not reported";
}

function formattedProfileDate(timestamp: number): string {
  const date = new Date(timestamp * 1_000);
  if (Number.isNaN(date.valueOf())) {
    return "Not reported";
  }
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

export function Calibration({ client, connectionState }: CalibrationProps) {
  const [context, setContext] = useState<CalibrationContext | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const loadGeneration = useRef(0);

  const [calibration, setCalibration] = useState<CalibrationEvent | null>(null);
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState<string | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const previewUrlRef = useRef<string | null>(null);
  const stepHeadingRef = useRef<HTMLHeadingElement>(null);
  const lastFocusedStepRef = useRef<string | null>(null);

  const connected = connectionState === "open";

  const loadContext = useCallback(async () => {
    const generation = loadGeneration.current + 1;
    loadGeneration.current = generation;
    setLoading(true);
    setError(null);
    try {
      const [settingsEvent, libraryEvent] = await Promise.all([
        client.request("get_settings"),
        client.request("list_library"),
      ]);
      const nextContext: CalibrationContext = {
        effectiveSettings: requireSettings(settingsEvent),
        gestures: requireLibrary(libraryEvent),
      };
      if (loadGeneration.current === generation) {
        setContext(nextContext);
      }
    } catch (requestError) {
      if (loadGeneration.current === generation) {
        setError(errorMessage(requestError));
      }
    } finally {
      if (loadGeneration.current === generation) {
        setLoading(false);
      }
    }
  }, [client]);

  useEffect(() => {
    if (connectionState === "open") {
      void loadContext();
      return;
    }
    loadGeneration.current += 1;
    setContext(null);
    setLoading(false);
    setError(null);
  }, [connectionState, loadContext]);

  useEffect(
    () => () => {
      loadGeneration.current += 1;
    },
    [],
  );

  const revokePreviewUrl = useCallback(() => {
    if (previewUrlRef.current !== null) {
      URL.revokeObjectURL(previewUrlRef.current);
      previewUrlRef.current = null;
    }
  }, []);

  // Live calibration events: subscribe whenever connected, and reconcile
  // with the daemon's authoritative state (in case a session is already
  // running -- e.g. this screen was left and re-opened mid-flow).
  useEffect(() => {
    if (!connected) {
      setCalibration(null);
      setStartError(null);
      return;
    }

    let active = true;
    const unsubscribe = client.on("calibration", (event) => {
      if (!active || event.type !== "calibration") {
        return;
      }
      setCalibration(event);
      if (event.complete) {
        void loadContext();
      }
    });

    void client
      .request("get_calibration_state")
      .then((event) => {
        if (active && event.type === "calibration") {
          setCalibration(event);
        }
      })
      .catch(() => {
        /* Live "calibration" broadcasts still drive the screen if this fails. */
      });

    return () => {
      active = false;
      unsubscribe();
    };
  }, [client, connected, loadContext]);

  // Live preview only while a calibration session is actually running.
  useEffect(() => {
    revokePreviewUrl();
    setPreviewUrl(null);
    if (!connected || !(calibration?.active ?? false)) {
      return;
    }

    let active = true;
    const unsubscribe = client.onPreviewFrame((frame) => {
      if (!active) {
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
    client.send("set_preview", { enabled: true });

    return () => {
      active = false;
      unsubscribe();
      client.send("set_preview", { enabled: false });
      revokePreviewUrl();
    };
  }, [calibration?.active, client, connected, revokePreviewUrl]);

  // Move focus to the step heading whenever the step changes, for screen
  // reader and keyboard users tracking a multi-step flow.
  useEffect(() => {
    const step = calibration?.step ?? "";
    if (step !== "" && step !== lastFocusedStepRef.current) {
      lastFocusedStepRef.current = step;
      stepHeadingRef.current?.focus();
    }
    if (step === "") {
      lastFocusedStepRef.current = null;
    }
  }, [calibration?.step]);

  const startCalibration = useCallback(async () => {
    if (starting) {
      return;
    }
    setStarting(true);
    setStartError(null);
    try {
      const reply = await client.request("start_calibration");
      if (reply.type === "ack" && !reply.ok) {
        setStartError(reply.error || "Calibration could not start.");
        return;
      }
      const state = await client.request("get_calibration_state");
      if (state.type === "calibration") {
        setCalibration(state);
      }
    } catch (requestError) {
      setStartError(
        `Calibration could not start: ${errorMessage(requestError)}`,
      );
    } finally {
      setStarting(false);
    }
  }, [client, starting]);

  const continueStep = useCallback(() => {
    client.send("advance_calibration");
  }, [client]);

  const cancelCalibration = useCallback(() => {
    client.send("cancel_calibration");
    setCalibration(null);
    setStartError(null);
  }, [client]);

  const active = calibration?.active ?? false;
  const justCompleted = calibration?.complete ?? false;
  const step = calibration?.step ?? "";
  const stepIndex = STEP_ORDER.indexOf(step);
  const autoAdvancing = AUTO_ADVANCE_STEPS.has(step);

  return (
    <>
      <ScreenHeader
        title="Calibration"
        description="Personalize tracking for your hand, camera position, motion, and lighting."
      />

      {!connected ? (
        <DaemonNotice />
      ) : loading && context === null ? (
        <LoadingState>Checking calibration context…</LoadingState>
      ) : error !== null ? (
        <ErrorState
          title="Calibration details could not be loaded"
          detail={error}
          onRetry={() => void loadContext()}
        />
      ) : context === null ? (
        <div className="state-panel" role="status">
          <strong>No calibration details were returned</strong>
          <span>The daemon is connected, but its context is empty.</span>
        </div>
      ) : (
        <div className="calibration-layout">
          <section className="panel calibration-status" aria-labelledby="profile-title">
            <div className="section-heading">
              <div>
                <h2 id="profile-title">Active profile</h2>
                <p>Saved calibration state reported by the running daemon.</p>
              </div>
              <span
                className="status-badge"
                data-tone={
                  context.effectiveSettings.has_calibration_profile
                    ? "success"
                    : "neutral"
                }
              >
                {context.effectiveSettings.has_calibration_profile
                  ? "Calibrated"
                  : "Not calibrated"}
              </span>
            </div>
            {context.effectiveSettings.has_calibration_profile ? (
              <p>
                AirControl is using a saved calibration profile for this camera
                session.
              </p>
            ) : (
              <p>
                No saved calibration profile is active. Run guided calibration below
                before recording custom gestures.
              </p>
            )}
            {context.effectiveSettings.calibration === undefined ? null : (
              <dl className="settings-grid calibration-details">
                <div>
                  <dt>Normalized hand size</dt>
                  <dd className="mono">
                    {String(context.effectiveSettings.calibration.hand_size)}
                  </dd>
                </div>
                <div>
                  <dt>Lighting check</dt>
                  <dd>
                    {context.effectiveSettings.calibration.lighting_acceptable
                      ? "Acceptable"
                      : "Needs attention"}
                  </dd>
                </div>
                <div>
                  <dt>Profile saved</dt>
                  <dd>
                    {formattedProfileDate(
                      context.effectiveSettings.calibration.created_at,
                    )}
                  </dd>
                </div>
              </dl>
            )}
          </section>

          <section className="panel" aria-labelledby="calibration-does-title">
            <div className="section-heading">
              <div>
                <h2 id="calibration-does-title">What calibration does</h2>
                <p>A guided sequence tailored to your camera setup.</p>
              </div>
            </div>
            <p>
              Calibration measures your usable camera area, hand size, motion
              speed, ordinary desk movement, and lighting. The saved profile helps
              AirControl personalize arming and gesture thresholds while preserving
              its “do nothing when uncertain” safety rule.
            </p>
          </section>

          <section className="panel" aria-labelledby="run-calibration-title">
            <div className="section-heading">
              <div>
                <h2 id="run-calibration-title">Run guided calibration</h2>
                <p>Everything happens right here — no other app or terminal needed.</p>
              </div>
            </div>

            {!active && !justCompleted ? (
              <div className="calibration-runner">
                <ol className="instruction-list">
                  <li>
                    <span aria-hidden="true">1</span>
                    <p>Frame the area where you naturally hold your hand while gesturing.</p>
                  </li>
                  <li>
                    <span aria-hidden="true">2</span>
                    <p>Hold a relaxed open palm still for a quick hand-size snapshot.</p>
                  </li>
                  <li>
                    <span aria-hidden="true">3</span>
                    <p>Perform a few deliberate swipes so AirControl learns your motion speed.</p>
                  </li>
                  <li>
                    <span aria-hidden="true">4</span>
                    <p>Work at your desk normally for a short capture of incidental motion.</p>
                  </li>
                  <li>
                    <span aria-hidden="true">5</span>
                    <p>Hold still briefly for a lighting check, then you are done.</p>
                  </li>
                </ol>
                {startError !== null ? (
                  <ErrorState
                    title="Calibration could not start"
                    detail={startError}
                    onRetry={() => void startCalibration()}
                  />
                ) : null}
                <button
                  className="button button-primary"
                  type="button"
                  disabled={starting}
                  onClick={() => void startCalibration()}
                >
                  {starting ? "Starting…" : "Start calibration"}
                </button>
              </div>
            ) : justCompleted ? (
              <div className="calibration-runner">
                <div className="success-message" role="status">
                  <strong>Calibration complete.</strong> Your active profile above now
                  reflects this session.
                </div>
                <button
                  className="button button-secondary"
                  type="button"
                  onClick={() => void startCalibration()}
                >
                  Calibrate again
                </button>
              </div>
            ) : (
              <div className="calibration-runner">
                <div className="camera-hero-frame recording-preview" data-live={previewUrl !== null}>
                  {previewUrl === null ? (
                    <div className="camera-hero-placeholder" role="status">
                      Waiting for live camera preview…
                    </div>
                  ) : (
                    <img
                      src={previewUrl}
                      alt="Live camera preview with hand-skeleton overlay while calibrating"
                    />
                  )}
                  {previewUrl === null ? null : (
                    <span className="live-badge">
                      <span aria-hidden="true" />
                      LIVE
                    </span>
                  )}
                </div>

                <div className="recording-status-row">
                  <span>
                    Step {stepIndex === -1 ? "–" : stepIndex + 1} of {STEP_ORDER.length}
                  </span>
                </div>

                <h3
                  ref={stepHeadingRef}
                  tabIndex={-1}
                  id="calibration-step-title"
                >
                  {STEP_TITLES[step] || "Calibrating…"}
                </h3>
                <p role="status" aria-live="polite">
                  {calibration?.instruction ?? "Preparing calibration…"}
                </p>

                <div className="recording-progress" role="status" aria-live="polite">
                  <progress
                    aria-label={`${STEP_TITLES[step] || "Calibration"} progress`}
                    value={calibration?.progress ?? 0}
                    max={1}
                  />
                  {autoAdvancing ? (
                    <p className="recording-progress-hint">
                      Sampling automatically — keep going, no need to click anything.
                    </p>
                  ) : (
                    <p className="recording-progress-hint">
                      When you are ready, press Continue.
                    </p>
                  )}
                </div>

                <div className="dialog-actions">
                  {autoAdvancing ? null : (
                    <button
                      className="button button-primary"
                      type="button"
                      onClick={continueStep}
                    >
                      Continue
                    </button>
                  )}
                  <button
                    className="button button-secondary"
                    type="button"
                    onClick={cancelCalibration}
                  >
                    Cancel
                  </button>
                </div>
              </div>
            )}
          </section>

          <section className="panel" aria-labelledby="tuning-title">
            <div className="section-heading">
              <div>
                <h2 id="tuning-title">Tuning</h2>
                <p>
                  Personalize pointer feel, click sensitivity, and gesture
                  timing. Changes save locally and apply to the running daemon.
                </p>
              </div>
            </div>
            <AdvancedTuning client={client} variant="expanded" />
          </section>

          <section className="panel calibration-context" aria-labelledby="context-title">
            <div className="section-heading">
              <div>
                <h2 id="context-title">Current context</h2>
                <p>Camera, control, and gesture-library facts reported by the daemon.</p>
              </div>
            </div>
            <dl className="settings-grid">
              <div>
                <dt>Camera index</dt>
                <dd>{reportedValue(context.effectiveSettings.camera_index)}</dd>
              </div>
              <div>
                <dt>Clutch mode</dt>
                <dd>{reportedValue(context.effectiveSettings.clutch_mode)}</dd>
              </div>
              <div>
                <dt>Custom gestures</dt>
                <dd>{context.gestures.length}</dd>
              </div>
            </dl>
            {context.gestures.length === 0 ? (
              <div className="state-panel" role="status">
                <strong>No custom gestures recorded yet</strong>
                <span>
                  Calibration status is reported independently in the active profile
                  section above.
                </span>
              </div>
            ) : null}
          </section>
        </div>
      )}
    </>
  );
}

export default Calibration;
