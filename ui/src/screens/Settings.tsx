import { useCallback, useEffect, useRef, useState } from "react";

import { useAppSettings } from "../lib/app-settings";
import type {
  ClickMode,
  DominantHand,
  ServerEvent,
  SettingsEvent,
} from "../lib/types";
import {
  DaemonNotice,
  ErrorState,
  LoadingState,
  ScreenHeader,
  SettingRange,
} from "../lib/ui";
import type { AirControlClient, ConnectionState } from "../lib/ws";
import { AdvancedTuning } from "./AdvancedTuning";

type ScreenClient = Pick<AirControlClient, "request">;

export interface SettingsProps {
  client: ScreenClient;
  connectionState: ConnectionState;
}

const SETTING_LABELS: Readonly<Record<string, string>> = {
  camera_index: "Camera index",
  clutch_mode: "Clutch mode",
  gate_thresholds: "Effective gate thresholds",
  store_db_path: "Database path",
  t1: "Top match",
  t2: "Match margin",
  t3: "Incidental-motion distance",
};

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Unknown settings error";
}

function requireEffectiveSettings(event: ServerEvent): SettingsEvent["payload"] {
  if (event.type === "settings") {
    return event.payload;
  }
  if (event.type === "ack" && !event.ok) {
    throw new Error(event.error || "Effective settings are unavailable");
  }
  throw new Error(`Expected settings, received ${event.type}`);
}

function requireSuccessfulAck(event: ServerEvent): void {
  if (event.type !== "ack") {
    throw new Error(`Expected acknowledgement, received ${event.type}`);
  }
  if (!event.ok) {
    throw new Error(event.error || "The daemon did not confirm deletion");
  }
}

function settingLabel(key: string): string {
  const known = SETTING_LABELS[key];
  if (known !== undefined) {
    return known;
  }
  const words = key.replaceAll("_", " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

function settingValue(value: unknown): string {
  if (value === null || value === undefined) {
    return "Not reported";
  }
  if (
    typeof value === "number" ||
    typeof value === "string" ||
    typeof value === "boolean"
  ) {
    return String(value);
  }
  if (Array.isArray(value)) {
    return value.map(settingValue).join(", ");
  }
  if (typeof value === "object") {
    return Object.entries(value)
      .map(([key, nested]) => `${settingLabel(key)}: ${settingValue(nested)}`)
      .join(" · ");
  }
  return String(value);
}

export function Settings({ client, connectionState }: SettingsProps) {
  const {
    settings: appSettings,
    loading: appSettingsLoading,
    error: appSettingsError,
    refresh: refreshAppSettings,
    updateSetting,
  } = useAppSettings();
  const [effectiveSettings, setEffectiveSettings] = useState<
    SettingsEvent["payload"] | null
  >(null);
  const [effectiveLoading, setEffectiveLoading] = useState(false);
  const [effectiveError, setEffectiveError] = useState<string | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [deleteSuccess, setDeleteSuccess] = useState<string | null>(null);
  const loadGeneration = useRef(0);
  const confirmDialogRef = useRef<HTMLDialogElement>(null);

  const loadEffectiveSettings = useCallback(async () => {
    const generation = loadGeneration.current + 1;
    loadGeneration.current = generation;
    setEffectiveLoading(true);
    setEffectiveError(null);
    try {
      const event = await client.request("get_settings");
      const payload = requireEffectiveSettings(event);
      if (loadGeneration.current === generation) {
        setEffectiveSettings(payload);
      }
    } catch (error) {
      if (loadGeneration.current === generation) {
        setEffectiveError(errorMessage(error));
      }
    } finally {
      if (loadGeneration.current === generation) {
        setEffectiveLoading(false);
      }
    }
  }, [client]);

  useEffect(() => {
    if (connectionState === "open") {
      void loadEffectiveSettings();
      return;
    }
    loadGeneration.current += 1;
    setEffectiveSettings(null);
    setEffectiveLoading(false);
    setEffectiveError(null);
  }, [connectionState, loadEffectiveSettings]);

  useEffect(
    () => () => {
      loadGeneration.current += 1;
    },
    [],
  );

  useEffect(() => {
    const dialog = confirmDialogRef.current;
    if (!confirmOpen || dialog === null || dialog.open) {
      return;
    }
    if (typeof dialog.showModal === "function") {
      try {
        dialog.showModal();
      } catch {
        dialog.setAttribute("open", "");
      }
    } else {
      dialog.setAttribute("open", "");
    }
    dialog.querySelector<HTMLButtonElement>(".button-secondary")?.focus();
  }, [confirmOpen]);

  const closeConfirmation = () => {
    const dialog = confirmDialogRef.current;
    setConfirmOpen(false);
    if (dialog !== null && dialog.open && typeof dialog.close === "function") {
      dialog.close();
    } else {
      dialog?.removeAttribute("open");
    }
  };

  const confirmDeleteEverything = async () => {
    setDeleting(true);
    setDeleteError(null);
    setDeleteSuccess(null);
    try {
      const event = await client.request("delete_everything");
      requireSuccessfulAck(event);
      closeConfirmation();
      setDeleteSuccess(
        "All local AirControl data was deleted. Restart AirControl before relying on reset runtime settings.",
      );
      await loadEffectiveSettings();
    } catch (error) {
      setDeleteError(errorMessage(error));
    } finally {
      setDeleting(false);
    }
  };

  if (connectionState !== "open") {
    return (
      <>
        <ScreenHeader
          title="Settings"
          description="Tune gesture response and review the daemon configuration in effect."
        />
        <DaemonNotice />
      </>
    );
  }

  const effectiveEntries =
    effectiveSettings === null ? [] : Object.entries(effectiveSettings);
  const databasePath =
    typeof effectiveSettings?.store_db_path === "string"
      ? effectiveSettings.store_db_path
      : "Not reported";

  return (
    <>
      <ScreenHeader
        title="Settings"
        description="Tune gesture response and review the daemon configuration in effect."
      />

      <div className="settings-sections">
        <section className="panel settings-panel" aria-labelledby="response-title">
          <div className="section-heading">
            <div>
              <h2 id="response-title">Response</h2>
              <p>
                Runtime-supported changes are saved locally and applied to the
                running daemon.
              </p>
            </div>
          </div>

          {appSettingsLoading && appSettings === null ? (
            <LoadingState>Loading app settings…</LoadingState>
          ) : appSettings === null && appSettingsError !== null ? (
            <ErrorState
              title="App settings could not be loaded"
              detail={appSettingsError}
              onRetry={() => void refreshAppSettings()}
            />
          ) : appSettings === null ? (
            <div className="state-panel" role="status">
              <strong>No app settings were returned</strong>
              <span>The daemon is connected but did not provide saved values.</span>
            </div>
          ) : (
            <>
              <div className="settings-control-list">
              {appSettingsError === null ? null : (
                <ErrorState
                  title="A setting could not be saved"
                  detail={appSettingsError}
                  onRetry={() => void refreshAppSettings()}
                />
              )}
              <SettingRange
                id="settings-sensitivity"
                label="Sensitivity"
                helper="Adjust how sensitive gesture detection is"
                value={appSettings.sensitivity}
                min={0}
                max={100}
                step={1}
                onCommit={async (value) => {
                  await updateSetting("sensitivity", value);
                }}
              />
              <SettingRange
                id="settings-smoothing"
                label="Smoothing"
                helper="Reduce jitter for smoother cursor movement"
                value={appSettings.smoothing}
                min={0}
                max={100}
                step={1}
                onCommit={async (value) => {
                  await updateSetting("smoothing", value);
                }}
              />
              <SettingRange
                id="settings-cursor-speed"
                label="Cursor speed"
                helper="Scale how far the pointer moves with your hand"
                value={appSettings.cursor_speed}
                min={0.25}
                max={3}
                step={0.05}
                formatValue={(value) => `${value.toFixed(2)}x`}
                onCommit={async (value) => {
                  await updateSetting("cursor_speed", value);
                }}
              />
              <div className="setting-field">
                <label htmlFor="settings-dominant-hand">Dominant hand</label>
                <select
                  id="settings-dominant-hand"
                  className="select-control"
                  value={appSettings.dominant_hand}
                  onChange={(event) => {
                    const hand = event.target.value as DominantHand;
                    void updateSetting("dominant_hand", hand);
                  }}
                >
                  <option value="right">Right</option>
                  <option value="left">Left</option>
                </select>
                <p>
                  Saved preference only; the current daemon does not report live
                  dominant-hand use.
                </p>
              </div>
              <div className="setting-field">
                <label htmlFor="settings-click-mode">Click mode</label>
                <select
                  id="settings-click-mode"
                  className="select-control"
                  value={appSettings.click_mode ?? "single"}
                  onChange={(event) => {
                    const clickMode = event.target.value as ClickMode;
                    void updateSetting("click_mode", clickMode);
                  }}
                >
                  <option value="single">Single hand (pinch to click)</option>
                  <option value="two_hand">Two hands (point + pinch)</option>
                </select>
                <p>
                  In two-hand mode, point with your dominant hand and pinch with
                  your other hand to click, so clicking never moves the cursor.
                </p>
              </div>
              </div>

              <AdvancedTuning client={client} variant="collapsible" />
            </>
          )}
        </section>

        <section className="panel settings-panel" aria-labelledby="effective-title">
          <div className="section-heading">
            <div>
              <h2 id="effective-title">Effective daemon configuration</h2>
              <p>Read-only values currently reported by AirControl.</p>
            </div>
          </div>

          {effectiveLoading && effectiveSettings === null ? (
            <LoadingState>Loading effective configuration…</LoadingState>
          ) : effectiveError !== null ? (
            <ErrorState
              title="Effective configuration could not be loaded"
              detail={effectiveError}
              onRetry={() => void loadEffectiveSettings()}
            />
          ) : effectiveEntries.length === 0 ? (
            <div className="state-panel" role="status">
              <strong>No effective configuration was returned</strong>
              <span>The daemon did not provide any readable config values.</span>
            </div>
          ) : (
            <dl className="settings-grid">
              {effectiveEntries.map(([key, value]) => (
                <div key={key}>
                  <dt>{settingLabel(key)}</dt>
                  <dd className={key === "store_db_path" ? "mono" : undefined}>
                    {settingValue(value)}
                  </dd>
                </div>
              ))}
            </dl>
          )}
        </section>

        <section className="panel settings-panel privacy" aria-labelledby="privacy-title">
          <div className="section-heading">
            <div>
              <h2 id="privacy-title">Privacy and local data</h2>
              <p>All processing is on-device.</p>
            </div>
          </div>
          <p>
            Camera-derived recordings contain hand-skeleton coordinates, never
            video or camera frames. Local data also includes mappings, settings,
            calibration metrics, and reliability statistics.
          </p>
          <p>
            Database path: <span className="mono">{databasePath}</span>
          </p>
          {deleteSuccess === null ? null : (
            <p className="success-message" role="status">
              {deleteSuccess}
            </p>
          )}
          <button
            className="button button-danger"
            type="button"
            onClick={() => {
              setDeleteError(null);
              setDeleteSuccess(null);
              setConfirmOpen(true);
            }}
          >
            Delete everything
          </button>
        </section>
      </div>

      {confirmOpen ? (
        <dialog
          ref={confirmDialogRef}
          className="confirm-dialog"
          aria-labelledby="delete-everything-title"
          onCancel={(event) => {
            event.preventDefault();
            closeConfirmation();
          }}
          onClose={() => setConfirmOpen(false)}
        >
          <h2 id="delete-everything-title">Delete all AirControl data?</h2>
          <p>
            This permanently removes custom gestures, exemplars, mappings,
            calibration profiles, statistics, and saved app settings from this
            device. This cannot be undone.
          </p>
          {deleteError === null ? null : <p role="alert">{deleteError}</p>}
          <div className="dialog-actions">
            <button
              className="button button-secondary"
              type="button"
              disabled={deleting}
              onClick={closeConfirmation}
            >
              Cancel
            </button>
            <button
              className="button button-danger"
              type="button"
              disabled={deleting}
              onClick={() => void confirmDeleteEverything()}
            >
              {deleting ? "Deleting…" : "Yes, delete everything"}
            </button>
          </div>
        </dialog>
      ) : null}
    </>
  );
}

export default Settings;
