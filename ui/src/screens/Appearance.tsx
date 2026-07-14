import { applyThemePreference, useAppSettings } from "../lib/app-settings";
import type { ThemePreference } from "../lib/types";
import {
  DaemonNotice,
  ErrorState,
  LoadingState,
  ScreenHeader,
  ToggleSwitch,
} from "../lib/ui";
import type { ConnectionState } from "../lib/ws";

export interface AppearanceProps {
  connectionState: ConnectionState;
}

const THEME_OPTIONS: readonly {
  value: ThemePreference;
  label: string;
  description: string;
}[] = [
  {
    value: "light",
    label: "Light",
    description: "Clean light surfaces with dark, high-contrast text.",
  },
  {
    value: "dark",
    label: "Dark",
    description: "Deep blue-black surfaces for a quieter desktop presence.",
  },
  {
    value: "system",
    label: "System",
    description: "Follow the Windows light or dark appearance automatically.",
  },
];

export function Appearance({ connectionState }: AppearanceProps) {
  const { settings, loading, error, refresh, updateSetting } = useAppSettings();

  const chooseTheme = (preference: ThemePreference) => {
    applyThemePreference(preference);
    void updateSetting("theme", preference);
  };

  return (
    <>
      <ScreenHeader
        title="Appearance"
        description="Choose how AirControl fits into your Windows desktop."
      />

      {connectionState !== "open" ? (
        <DaemonNotice />
      ) : loading && settings === null ? (
        <LoadingState>Loading appearance settings…</LoadingState>
      ) : settings === null && error !== null ? (
        <ErrorState
          title="Appearance settings could not be loaded"
          detail={error}
          onRetry={() => void refresh()}
        />
      ) : settings === null ? (
        <div className="state-panel" role="status">
          <strong>No appearance settings were returned</strong>
          <span>The daemon is connected but did not provide saved preferences.</span>
        </div>
      ) : (
        <div className="appearance-layout">
          {error === null ? null : (
            <ErrorState
              title="An appearance setting could not be saved"
              detail={error}
              onRetry={() => void refresh()}
            />
          )}

          <section className="panel" aria-labelledby="theme-title">
            <div className="section-heading">
              <div>
                <h2 id="theme-title">Theme</h2>
                <p>The selected theme is applied immediately and saved locally.</p>
              </div>
            </div>
            <fieldset className="theme-options">
              <legend className="sr-only">Choose an AirControl theme</legend>
              {THEME_OPTIONS.map((option) => (
                <label
                  key={option.value}
                  className="theme-option"
                  data-selected={settings.theme === option.value}
                >
                  <input
                    type="radio"
                    name="aircontrol-theme"
                    value={option.value}
                    checked={settings.theme === option.value}
                    onChange={() => chooseTheme(option.value)}
                  />
                  <span
                    className="theme-swatch"
                    data-theme-preview={option.value}
                    aria-hidden="true"
                  >
                    <span />
                    <span />
                  </span>
                  <span className="theme-option-copy">
                    <strong>{option.label}</strong>
                    <span>{option.description}</span>
                  </span>
                </label>
              ))}
            </fieldset>
          </section>

          <section className="panel companion-setting" aria-labelledby="airy-title">
            <div>
              <h2 id="airy-title">Show Airy companion</h2>
              <p>
                Save whether Airy should appear when companion support is running.
                The daemon does not currently report whether a companion is active.
              </p>
              <span className="setting-state">
                {settings.airy_enabled ? "Preference on" : "Preference off"}
              </span>
            </div>
            <ToggleSwitch
              checked={settings.airy_enabled}
              label="Show Airy companion"
              onChange={(enabled) => {
                void updateSetting("airy_enabled", enabled);
              }}
            />
          </section>
        </div>
      )}
    </>
  );
}

export default Appearance;
