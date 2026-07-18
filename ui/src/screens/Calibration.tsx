import { useCallback, useEffect, useRef, useState } from "react";

import type { LibraryGesture, ServerEvent, SettingsEvent } from "../lib/types";
import {
  DaemonNotice,
  ErrorState,
  LoadingState,
  ScreenHeader,
} from "../lib/ui";
import type { AirControlClient, ConnectionState } from "../lib/ws";
import { AdvancedTuning } from "./AdvancedTuning";

type ScreenClient = Pick<AirControlClient, "request">;

export interface CalibrationProps {
  client: ScreenClient;
  connectionState: ConnectionState;
}

interface CalibrationContext {
  effectiveSettings: SettingsEvent["payload"];
  gestures: LibraryGesture[];
}

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

  const loadContext = useCallback(async () => {
    const generation = loadGeneration.current + 1;
    loadGeneration.current = generation;
    setLoading(true);
    setError(null);
    setContext(null);
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

  return (
    <>
      <ScreenHeader
        title="Calibration"
        description="Personalize tracking for your hand, camera position, motion, and lighting."
      />

      {connectionState !== "open" ? (
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
                No saved calibration profile is active. Run guided calibration before
                recording custom gestures.
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
                <p>Calibration cannot be launched from this screen.</p>
              </div>
            </div>
            <ol className="instruction-list">
              <li>
                <span aria-hidden="true">1</span>
                <p>Close the running camera session so calibration can use it.</p>
              </li>
              <li>
                <span aria-hidden="true">2</span>
                <p>
                  Open the AirControl folder and double-click <code>calibrate.cmd</code>.
                </p>
              </li>
              <li>
                <span aria-hidden="true">3</span>
                <p>Follow the framing, motion, normal-work, and lighting prompts.</p>
              </li>
              <li>
                <span aria-hidden="true">4</span>
                <p>When it finishes, start AirControl again with <code>app.cmd</code>.</p>
              </li>
            </ol>
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
