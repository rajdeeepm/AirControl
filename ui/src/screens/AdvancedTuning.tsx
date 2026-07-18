import { useState } from "react";

import { useAppSettings } from "../lib/app-settings";
import type { AdvancedAppSettings, AppSettings, ServerEvent } from "../lib/types";
import { ErrorState, LoadingState, SettingRange, ToggleSwitch } from "../lib/ui";
import type { AirControlClient } from "../lib/ws";

type ScreenClient = Pick<AirControlClient, "request">;

type AdvancedSettingKey = keyof AdvancedAppSettings;
type AdvancedUpdateKey = AdvancedSettingKey | "drag_lock_enabled";

export interface AdvancedTuningProps {
  client: ScreenClient;
  /**
   * "collapsible" wraps the controls in a closed <details> (used on Settings);
   * "expanded" renders them inline and visible by default (used on Calibration).
   */
  variant?: "collapsible" | "expanded";
}

const ADVANCED_DEFAULTS: Readonly<Required<AdvancedAppSettings>> = {
  pointer_responsiveness: 20,
  click_engage: 0.45,
  click_release: 0.6,
  pinch_approach: 0.75,
  pinch_drag_release: 0.08,
  arm_hold_seconds: 0.7,
  pause_hold_seconds: 0.55,
  scroll_speed: 50,
  swipe_distance: 0.9,
};

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Unknown settings error";
}

function requireSettingAck(event: ServerEvent): void {
  if (event.type !== "ack") {
    throw new Error(`Expected acknowledgement, received ${event.type}`);
  }
  if (!event.ok) {
    throw new Error(event.error || "The setting could not be saved");
  }
}

function requireAppSettingsEvent(event: ServerEvent): void {
  if (event.type === "app_settings") {
    return;
  }
  if (event.type === "ack" && !event.ok) {
    throw new Error(event.error || "App settings could not be reset");
  }
  throw new Error(`Expected app_settings, received ${event.type}`);
}

function advancedSettingValue(
  settings: AppSettings,
  key: AdvancedSettingKey,
): number {
  const advanced = settings as AppSettings & AdvancedAppSettings;
  const value = advanced[key];
  return typeof value === "number" && Number.isFinite(value)
    ? value
    : ADVANCED_DEFAULTS[key];
}

export function AdvancedTuning({ client, variant = "expanded" }: AdvancedTuningProps) {
  const {
    settings: appSettings,
    loading,
    error: settingsError,
    refresh,
  } = useAppSettings();
  const [advancedError, setAdvancedError] = useState<string | null>(null);
  const [advancedSuccess, setAdvancedSuccess] = useState<string | null>(null);
  const [resetting, setResetting] = useState(false);
  const [revision, setRevision] = useState(0);

  const updateAdvancedSetting = async (
    key: AdvancedUpdateKey,
    value: number | boolean,
  ) => {
    setAdvancedError(null);
    setAdvancedSuccess(null);
    try {
      const event = await client.request("set_app_setting", { key, value });
      requireSettingAck(event);
      await refresh();
    } catch (error) {
      await refresh();
      setRevision((current) => current + 1);
      setAdvancedError(errorMessage(error));
    }
  };

  const resetAdvancedSettings = async () => {
    setResetting(true);
    setAdvancedError(null);
    setAdvancedSuccess(null);
    try {
      const event = await client.request("reset_app_settings");
      requireAppSettingsEvent(event);
      await refresh();
      setAdvancedSuccess("App settings were reset to their defaults.");
    } catch (error) {
      setAdvancedError(errorMessage(error));
    } finally {
      setResetting(false);
    }
  };

  let body: React.ReactNode;
  if (loading && appSettings === null) {
    body = <LoadingState>Loading tuning controls…</LoadingState>;
  } else if (appSettings === null && settingsError !== null) {
    body = (
      <ErrorState
        title="Tuning controls could not be loaded"
        detail={settingsError}
        onRetry={() => void refresh()}
      />
    );
  } else if (appSettings === null) {
    body = (
      <div className="state-panel" role="status">
        <strong>No app settings were returned</strong>
        <span>The daemon is connected but did not provide saved values.</span>
      </div>
    );
  } else {
    body = (
      <div className="settings-panel" key={revision}>
        <p>
          Fine-tune pointer, click, and gesture behavior. Changes are saved
          locally and applied to the running daemon after a short pause.
        </p>

        {advancedError === null ? null : (
          <ErrorState
            title="Advanced tuning could not be saved"
            detail={advancedError}
            onRetry={() => void refresh()}
          />
        )}
        {advancedSuccess === null ? null : (
          <p className="success-message" role="status">
            {advancedSuccess}
          </p>
        )}

        <fieldset className="settings-panel">
          <legend>Pointer</legend>
          <SettingRange
            id="settings-pointer-responsiveness"
            label="Pointer responsiveness"
            helper="Higher values make the pointer react faster to changes in hand direction."
            value={advancedSettingValue(appSettings, "pointer_responsiveness")}
            min={0}
            max={100}
            step={1}
            onCommit={(value) =>
              updateAdvancedSetting("pointer_responsiveness", value)
            }
          />
        </fieldset>

        <fieldset className="settings-panel">
          <legend>Click</legend>
          <div className="setting-field companion-setting">
            <div>
              <label id="settings-drag-lock-label" className="field-label">
                Drag lock
              </label>
              <p>
                In two-hand mode, hold the click-hand pinch to lock the button
                down. Pinch again to drop.
              </p>
              <span className="setting-state">
                {(appSettings.drag_lock_enabled ?? true) ? "Enabled" : "Disabled"}
              </span>
            </div>
            <ToggleSwitch
              checked={appSettings.drag_lock_enabled ?? true}
              label="Drag lock"
              labelledBy="settings-drag-lock-label"
              onChange={(enabled) => {
                void updateAdvancedSetting("drag_lock_enabled", enabled);
              }}
            />
          </div>
          <SettingRange
            id="settings-click-engage"
            label="Click engage distance"
            helper="How close the pinch must be to press; keep this below the release distance."
            value={advancedSettingValue(appSettings, "click_engage")}
            min={0.2}
            max={0.8}
            step={0.01}
            formatValue={(value) => `${value.toFixed(2)} palms`}
            onCommit={(value) => updateAdvancedSetting("click_engage", value)}
          />
          <SettingRange
            id="settings-click-release"
            label="Click release distance"
            helper="How far the pinch must open to release; keep this above the engage distance."
            value={advancedSettingValue(appSettings, "click_release")}
            min={0.3}
            max={1.2}
            step={0.01}
            formatValue={(value) => `${value.toFixed(2)} palms`}
            onCommit={(value) => updateAdvancedSetting("click_release", value)}
          />
          <SettingRange
            id="settings-pinch-approach"
            label="Pinch approach distance"
            helper="Freezes pointer movement as a single-hand pinch approaches the click point."
            value={advancedSettingValue(appSettings, "pinch_approach")}
            min={0.43}
            max={1.5}
            step={0.01}
            formatValue={(value) => `${value.toFixed(2)} palms`}
            onCommit={(value) => updateAdvancedSetting("pinch_approach", value)}
          />
          <SettingRange
            id="settings-pinch-drag-release"
            label="Drag release motion"
            helper="How far a held pinch must move before drag motion begins."
            value={advancedSettingValue(appSettings, "pinch_drag_release")}
            min={0.01}
            max={0.5}
            step={0.01}
            formatValue={(value) => `${value.toFixed(2)} palms`}
            onCommit={(value) =>
              updateAdvancedSetting("pinch_drag_release", value)
            }
          />
        </fieldset>

        <fieldset className="settings-panel">
          <legend>Gestures</legend>
          <SettingRange
            id="settings-arm-hold-seconds"
            label="Arm hold time"
            helper="How long the wake pose must be held before gesture control arms."
            value={advancedSettingValue(appSettings, "arm_hold_seconds")}
            min={0.1}
            max={3}
            step={0.05}
            formatValue={(value) => `${value.toFixed(2)} s`}
            onCommit={(value) =>
              updateAdvancedSetting("arm_hold_seconds", value)
            }
          />
          <SettingRange
            id="settings-pause-hold-seconds"
            label="Pause hold time"
            helper="How long the pause pose must be held before gesture control pauses."
            value={advancedSettingValue(appSettings, "pause_hold_seconds")}
            min={0.1}
            max={3}
            step={0.05}
            formatValue={(value) => `${value.toFixed(2)} s`}
            onCommit={(value) =>
              updateAdvancedSetting("pause_hold_seconds", value)
            }
          />
          <SettingRange
            id="settings-scroll-speed"
            label="Scroll speed"
            helper="Higher values produce more scroll notches for the same hand travel."
            value={advancedSettingValue(appSettings, "scroll_speed")}
            min={0}
            max={100}
            step={1}
            onCommit={(value) => updateAdvancedSetting("scroll_speed", value)}
          />
          <SettingRange
            id="settings-swipe-distance"
            label="Swipe distance"
            helper="How far the hand must travel before a window swipe fires."
            value={advancedSettingValue(appSettings, "swipe_distance")}
            min={0.3}
            max={2}
            step={0.05}
            formatValue={(value) => `${value.toFixed(2)} palms`}
            onCommit={(value) => updateAdvancedSetting("swipe_distance", value)}
          />
        </fieldset>

        <div className="setting-field">
          <button
            className="button button-secondary"
            type="button"
            disabled={resetting}
            onClick={() => void resetAdvancedSettings()}
          >
            {resetting
              ? "Resetting app settings…"
              : "Reset advanced settings to defaults"}
          </button>
          <p>
            Reset restores every app setting, including the standard Response
            controls, to its shipped default.
          </p>
        </div>
      </div>
    );
  }

  if (variant === "collapsible") {
    return (
      <details id="settings-advanced-tuning" className="quick-start-collapsible">
        <summary>Advanced tuning</summary>
        {body}
      </details>
    );
  }
  return body as React.ReactElement;
}

export default AdvancedTuning;
