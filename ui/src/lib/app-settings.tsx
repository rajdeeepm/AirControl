import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import type {
  AppSettings,
  ServerEvent,
  ThemePreference,
} from "./types";
import type { AirControlClient, ConnectionState } from "./ws";

type SettingsClient = Pick<AirControlClient, "on" | "request">;
type AppSettingKey = keyof AppSettings;
type AppSettingValue = AppSettings[AppSettingKey];

const APP_SETTING_KEYS: readonly AppSettingKey[] = [
  "sensitivity",
  "smoothing",
  "cursor_speed",
  "dominant_hand",
  "theme",
  "airy_enabled",
];

export const DEFAULT_APP_SETTINGS: AppSettings = {
  sensitivity: 50,
  smoothing: 64,
  cursor_speed: 1,
  dominant_hand: "right",
  theme: "system",
  airy_enabled: true,
};

interface AppSettingsContextValue {
  settings: AppSettings | null;
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
  updateSetting: (
    key: AppSettingKey,
    value: AppSettingValue,
  ) => Promise<boolean>;
}

const AppSettingsContext = createContext<AppSettingsContextValue | null>(null);

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Unknown settings error";
}

function requireAppSettings(event: ServerEvent): AppSettings {
  if (event.type === "app_settings") {
    return event.settings;
  }
  if (event.type === "ack" && !event.ok) {
    throw new Error(event.error || "App settings are unavailable");
  }
  throw new Error(`Expected app_settings, received ${event.type}`);
}

function requireSuccessfulAck(event: ServerEvent): void {
  if (event.type !== "ack") {
    throw new Error(`Expected acknowledgement, received ${event.type}`);
  }
  if (!event.ok) {
    throw new Error(event.error || "The setting could not be saved");
  }
}

function preferredColorSchemeQuery(): MediaQueryList | null {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
    return null;
  }
  return window.matchMedia("(prefers-color-scheme: dark)");
}

export function resolvedTheme(
  preference: ThemePreference,
  query: MediaQueryList | null = preferredColorSchemeQuery(),
): "light" | "dark" {
  if (preference === "system") {
    return query?.matches === true ? "dark" : "light";
  }
  return preference;
}

export function applyThemePreference(preference: ThemePreference): void {
  if (typeof document === "undefined") {
    return;
  }
  const theme = resolvedTheme(preference);
  document.documentElement.dataset.theme = theme;
  document.documentElement.style.colorScheme = theme;
}

export interface AppSettingsProviderProps {
  client: SettingsClient;
  connectionState: ConnectionState;
  children: ReactNode;
}

export function AppSettingsProvider({
  client,
  connectionState,
  children,
}: AppSettingsProviderProps) {
  const [settings, setSettings] = useState<AppSettings | null>(null);
  const [loading, setLoading] = useState(connectionState !== "closed");
  const [error, setError] = useState<string | null>(null);
  const confirmedSettingsRef = useRef<AppSettings | null>(null);
  const pendingSettingsRef = useRef<Partial<AppSettings>>({});
  const loadGeneration = useRef(0);
  const mutationGeneration = useRef<Record<AppSettingKey, number>>({
    sensitivity: 0,
    smoothing: 0,
    cursor_speed: 0,
    dominant_hand: 0,
    theme: 0,
    airy_enabled: 0,
  });

  const displaySettings = useCallback((confirmed: AppSettings) => {
    const displayed = {
      ...confirmed,
      ...pendingSettingsRef.current,
    } as AppSettings;
    setSettings(displayed);
  }, []);

  const acceptSettings = useCallback((next: AppSettings) => {
    confirmedSettingsRef.current = next;
    displaySettings(next);
    setError(null);
  }, [displaySettings]);

  const refresh = useCallback(async () => {
    const generation = loadGeneration.current + 1;
    loadGeneration.current = generation;
    setLoading(true);
    setError(null);
    try {
      const event = await client.request("get_app_settings");
      const next = requireAppSettings(event);
      if (loadGeneration.current === generation) {
        acceptSettings(next);
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
  }, [acceptSettings, client]);

  useEffect(() => {
    const unsubscribe = client.on("app_settings", (event) => {
      if (event.type === "app_settings") {
        acceptSettings(event.settings);
      }
    });
    return unsubscribe;
  }, [acceptSettings, client]);

  useEffect(() => {
    if (connectionState === "open") {
      void refresh();
      return;
    }
    loadGeneration.current += 1;
    for (const key of APP_SETTING_KEYS) {
      mutationGeneration.current[key] += 1;
    }
    pendingSettingsRef.current = {};
    if (confirmedSettingsRef.current !== null) {
      setSettings(confirmedSettingsRef.current);
    }
    setLoading(connectionState === "connecting");
  }, [connectionState, refresh]);

  useEffect(() => {
    const preference = settings?.theme ?? "system";
    const query = preferredColorSchemeQuery();
    const apply = () => applyThemePreference(preference);
    apply();
    if (preference !== "system" || query === null) {
      return;
    }
    query.addEventListener("change", apply);
    return () => query.removeEventListener("change", apply);
  }, [settings?.theme]);

  const updateSetting = useCallback(
    async (key: AppSettingKey, value: AppSettingValue): Promise<boolean> => {
      if (connectionState !== "open") {
        setError("Daemon not connected — the setting was not saved.");
        return false;
      }

      const mutation = mutationGeneration.current[key] + 1;
      mutationGeneration.current[key] = mutation;
      const confirmed = confirmedSettingsRef.current ?? DEFAULT_APP_SETTINGS;
      pendingSettingsRef.current = {
        ...pendingSettingsRef.current,
        [key]: value,
      };
      displaySettings(confirmed);
      setError(null);

      try {
        const event = await client.request("set_app_setting", { key, value });
        requireSuccessfulAck(event);
        if (mutationGeneration.current[key] === mutation) {
          const nextConfirmed = {
            ...(confirmedSettingsRef.current ?? confirmed),
            [key]: value,
          } as AppSettings;
          confirmedSettingsRef.current = nextConfirmed;
          const remaining = { ...pendingSettingsRef.current };
          delete remaining[key];
          pendingSettingsRef.current = remaining;
          displaySettings(nextConfirmed);
        }
        return true;
      } catch (requestError) {
        if (mutationGeneration.current[key] === mutation) {
          const remaining = { ...pendingSettingsRef.current };
          delete remaining[key];
          pendingSettingsRef.current = remaining;
          displaySettings(confirmedSettingsRef.current ?? confirmed);
          setError(errorMessage(requestError));
        }
        return false;
      }
    },
    [client, connectionState, displaySettings],
  );

  const contextValue = useMemo<AppSettingsContextValue>(
    () => ({ settings, loading, error, refresh, updateSetting }),
    [error, loading, refresh, settings, updateSetting],
  );

  return (
    <AppSettingsContext.Provider value={contextValue}>
      {children}
    </AppSettingsContext.Provider>
  );
}

export function useAppSettings(): AppSettingsContextValue {
  const context = useContext(AppSettingsContext);
  if (context === null) {
    throw new Error("useAppSettings must be used inside AppSettingsProvider");
  }
  return context;
}
