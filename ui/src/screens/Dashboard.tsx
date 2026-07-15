import { useCallback, useEffect, useRef, useState } from "react";

import { describeAction, mappingEnabled } from "../lib/actions";
import { useAppSettings } from "../lib/app-settings";
import type {
  CandidateEvent,
  LibraryGesture,
  ServerEvent,
  StatusEvent,
} from "../lib/types";
import {
  DaemonNotice,
  ErrorState,
  LoadingState,
  ScreenHeader,
  SettingRange,
  ToggleSwitch,
} from "../lib/ui";
import type { AirControlClient, ConnectionState } from "../lib/ws";

type DashboardClient = Pick<
  AirControlClient,
  "on" | "onPreviewFrame" | "send" | "request"
>;

export interface DashboardProps {
  client: DashboardClient;
  connectionState: ConnectionState;
  onNavigate: (screen: "Gestures" | "Calibration") => void;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Unknown dashboard error";
}

function requireLibrary(event: ServerEvent): LibraryGesture[] {
  if (event.type === "library") {
    return event.gestures;
  }
  if (event.type === "ack" && !event.ok) {
    throw new Error(event.error || "The gesture library is unavailable");
  }
  throw new Error(`Expected library, received ${event.type}`);
}

function trackingText(status: StatusEvent | null): string {
  if (status === null) {
    return "Waiting for tracking status…";
  }
  const visibility = status.hand_visible ? "Hand detected" : "No hand detected";
  return `${visibility} · ${status.status_text}`;
}

function QuickStartSteps() {
  const steps = [
    ["Position yourself", "Keep your upper body and gesture hand inside the camera view."],
    ["Calibrate", "Run calibrate.cmd so AirControl can learn your comfortable range."],
    ["Try gestures", "Start with the built-in arm, pause, pointer, and scroll gestures."],
    ["You're ready", "Arm AirControl only when the preview and tracking status look right."],
  ] as const;

  return (
    <ol className="quick-start-steps">
      {steps.map(([title, detail], index) => (
        <li key={title}>
          <span className="quick-start-number" aria-hidden="true">
            {index + 1}
          </span>
          <div>
            <strong>{title}</strong>
            <span>{detail}</span>
          </div>
        </li>
      ))}
    </ol>
  );
}

export function Dashboard({
  client,
  connectionState,
  onNavigate,
}: DashboardProps) {
  const {
    settings,
    loading: settingsLoading,
    error: settingsError,
    refresh: refreshSettings,
    updateSetting,
  } = useAppSettings();
  const [status, setStatus] = useState<StatusEvent | null>(null);
  const statusRef = useRef<StatusEvent | null>(null);
  const [latestCandidate, setLatestCandidate] = useState<CandidateEvent | null>(
    null,
  );
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const previewUrlRef = useRef<string | null>(null);
  const [gestures, setGestures] = useState<LibraryGesture[] | null>(null);
  const [libraryLoading, setLibraryLoading] = useState(
    connectionState === "open",
  );
  const [libraryError, setLibraryError] = useState<string | null>(null);
  const libraryGeneration = useRef(0);

  const revokePreviewUrl = useCallback(() => {
    if (previewUrlRef.current !== null) {
      URL.revokeObjectURL(previewUrlRef.current);
      previewUrlRef.current = null;
    }
  }, []);

  const loadLibrary = useCallback(async () => {
    const generation = libraryGeneration.current + 1;
    libraryGeneration.current = generation;
    setLibraryLoading(true);
    setLibraryError(null);
    try {
      const event = await client.request("list_library");
      const nextGestures = requireLibrary(event);
      if (libraryGeneration.current === generation) {
        setGestures(nextGestures);
      }
    } catch (requestError) {
      if (libraryGeneration.current === generation) {
        setLibraryError(errorMessage(requestError));
      }
    } finally {
      if (libraryGeneration.current === generation) {
        setLibraryLoading(false);
      }
    }
  }, [client]);

  useEffect(() => {
    revokePreviewUrl();
    setPreviewUrl(null);
    if (connectionState !== "open") {
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
  }, [client, connectionState, revokePreviewUrl]);

  useEffect(() => {
    if (connectionState !== "open") {
      statusRef.current = null;
      setStatus(null);
      setLatestCandidate(null);
      return;
    }

    const unsubscribeStatus = client.on("status", (event) => {
      if (event.type === "status") {
        statusRef.current = event;
        setStatus(event);
        if (!event.hand_visible) {
          setLatestCandidate(null);
        }
      }
    });
    const unsubscribeCandidate = client.on("candidate", (event) => {
      if (
        event.type === "candidate" &&
        statusRef.current?.hand_visible !== false
      ) {
        setLatestCandidate(event);
      }
    });
    client.send("get_status");

    return () => {
      unsubscribeStatus();
      unsubscribeCandidate();
    };
  }, [client, connectionState]);

  useEffect(() => {
    if (connectionState !== "open") {
      libraryGeneration.current += 1;
      setGestures(null);
      setLibraryLoading(false);
      setLibraryError(null);
      return;
    }
    void loadLibrary();
    return () => {
      libraryGeneration.current += 1;
    };
  }, [connectionState, loadLibrary]);

  const connected = connectionState === "open";
  const armed = status?.armed ?? false;
  const libraryEmpty = gestures !== null && gestures.length === 0;
  const confidence =
    latestCandidate === null
      ? "No confidence data yet"
      : `${(latestCandidate.confidence * 100).toFixed(2)}%`;

  return (
    <>
      <ScreenHeader
        title="Dashboard"
        description="Live tracking, essential controls, and the shortest path to a reliable setup."
      />

      {!connected ? (
        <DaemonNotice />
      ) : (
        <>
          <div className="dashboard-hero">
            <section className="camera-hero" aria-labelledby="camera-hero-title">
              <div className="camera-hero-heading">
                <div>
                  <h2 id="camera-hero-title">Camera preview</h2>
                  <p>Your annotated camera feed stays on this device and is never saved.</p>
                </div>
              </div>
              <div className="camera-hero-frame" data-live={previewUrl !== null}>
                {previewUrl === null ? (
                  <div className="camera-hero-placeholder" role="status">
                    Starting camera preview…
                  </div>
                ) : (
                  <img src={previewUrl} alt="Live camera preview with hand-skeleton overlay" />
                )}
                {previewUrl === null ? null : (
                  <span className="live-badge">
                    <span aria-hidden="true" />
                    LIVE
                  </span>
                )}
              </div>
              <div
                className="tracking-quality"
                data-visible={status?.hand_visible ?? false}
                role="status"
                aria-live="polite"
              >
                <span className="tracking-quality-mark" aria-hidden="true" />
                <span>{trackingText(status)}</span>
              </div>
            </section>

            <aside className="dashboard-controls" aria-label="Live controls">
              <div
                className="armed-control"
                data-armed={status?.armed ?? "unknown"}
              >
                <div>
                  <span id="dashboard-armed-label" className="control-label">
                    Gesture control
                  </span>
                  <strong>
                    {status === null ? "STATUS UNKNOWN" : armed ? "ARMED" : "IDLE"}
                  </strong>
                </div>
                <ToggleSwitch
                  checked={armed}
                  disabled={status === null}
                  label={armed ? "Pause gesture controls" : "Arm gesture controls"}
                  labelledBy="dashboard-armed-label"
                  onChange={() => client.send("toggle_arm")}
                />
              </div>

              <div className="confidence-readout">
                <span>Top-1 candidate confidence</span>
                <strong className={latestCandidate === null ? undefined : "mono"}>
                  {confidence}
                </strong>
              </div>

              <div className="undo-control">
                <div>
                  <strong>Undo last action</strong>
                  <span>Available for three seconds when the action is reversible.</span>
                </div>
                <button
                  className="button button-secondary"
                  type="button"
                  onClick={() => client.send("undo")}
                >
                  Undo
                </button>
              </div>

              <section
                className="dashboard-tuning"
                aria-labelledby="dashboard-tuning-title"
              >
                <h2 id="dashboard-tuning-title">Detection feel</h2>
                {settingsLoading && settings === null ? (
                  <LoadingState>Loading sensitivity and smoothing…</LoadingState>
                ) : null}
                {settingsError !== null ? (
                  <ErrorState
                    title="Settings could not be loaded or saved"
                    detail={settingsError}
                    onRetry={() => void refreshSettings()}
                  />
                ) : null}
                {!settingsLoading && settingsError === null && settings === null ? (
                  <div className="state-panel">
                    <strong>App settings are unavailable</strong>
                    <span>The daemon returned no persisted settings.</span>
                  </div>
                ) : null}
                {settings === null ? null : (
                  <div className="dashboard-range-list">
                    <SettingRange
                      id="dashboard-sensitivity"
                      label="Sensitivity"
                      helper="Adjust how sensitive gesture detection is"
                      value={settings.sensitivity}
                      min={0}
                      max={100}
                      step={1}
                      onCommit={async (value) => {
                        await updateSetting("sensitivity", Math.round(value));
                      }}
                    />
                    <SettingRange
                      id="dashboard-smoothing"
                      label="Smoothing"
                      helper="Reduce jitter for smoother cursor movement"
                      value={settings.smoothing}
                      min={0}
                      max={100}
                      step={1}
                      onCommit={async (value) => {
                        await updateSetting("smoothing", Math.round(value));
                      }}
                    />
                  </div>
                )}
              </section>
            </aside>
          </div>

          {libraryLoading && gestures === null ? (
            <LoadingState>Loading gesture mappings and setup state…</LoadingState>
          ) : null}
          {libraryError !== null ? (
            <ErrorState
              title="Gesture mappings could not be loaded"
              detail={libraryError}
              onRetry={() => void loadLibrary()}
            />
          ) : null}

          {gestures === null ? null : (
            <>
              {libraryEmpty ? (
                <section className="quick-start" aria-labelledby="quick-start-title">
                  <div className="section-heading">
                    <div>
                      <h2 id="quick-start-title">Quick Start</h2>
                      <p>Four steps to a dependable first gesture session.</p>
                    </div>
                  </div>
                  <QuickStartSteps />
                </section>
              ) : (
                <details className="quick-start quick-start-collapsible">
                  <summary>Quick Start</summary>
                  <QuickStartSteps />
                </details>
              )}

              <div className="dashboard-lower-grid">
                <section
                  className="mapping-summary"
                  aria-labelledby="mapping-summary-title"
                >
                  <div className="section-heading">
                    <div>
                      <h2 id="mapping-summary-title">Gesture mappings</h2>
                      <p>Your custom gestures and the actions they perform.</p>
                    </div>
                    <button
                      className="button button-secondary"
                      type="button"
                      onClick={() => onNavigate("Gestures")}
                    >
                      Customize Gestures
                    </button>
                  </div>
                  {libraryEmpty ? (
                    <div className="state-panel">
                      <strong>No custom gestures yet</strong>
                      <span>Run record.cmd to teach AirControl your first gesture.</span>
                    </div>
                  ) : (
                    <ul className="mapping-summary-list">
                      {gestures.map((gesture) => (
                        <li key={gesture.id}>
                          <span>{gesture.name}</span>
                          <strong>
                            {describeAction(gesture.mapping)}
                            {gesture.mapping !== null &&
                            !mappingEnabled(gesture.mapping)
                              ? " · Disabled"
                              : ""}
                          </strong>
                        </li>
                      ))}
                    </ul>
                  )}
                </section>

                <section
                  className="calibration-card"
                  aria-labelledby="calibration-card-title"
                >
                  <div className="calibration-card-icon" aria-hidden="true">
                    <svg viewBox="0 0 24 24" focusable="false">
                      <path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.64 5.64l2.12 2.12M16.24 16.24l2.12 2.12M18.36 5.64l-2.12 2.12M7.76 16.24l-2.12 2.12" />
                      <circle cx="12" cy="12" r="3.5" />
                    </svg>
                  </div>
                  <div>
                    <h2 id="calibration-card-title">Calibrate for your range</h2>
                    <p>
                      Guided calibration adapts tracking to your hand and comfortable
                      movement. It cannot be launched from this UI; run calibrate.cmd.
                    </p>
                  </div>
                  <button
                    className="button button-primary"
                    type="button"
                    onClick={() => onNavigate("Calibration")}
                  >
                    View calibration guide
                  </button>
                </section>
              </div>
            </>
          )}
        </>
      )}
    </>
  );
}

export default Dashboard;
