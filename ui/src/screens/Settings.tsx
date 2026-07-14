import { useCallback, useEffect, useRef, useState } from "react";
import type { SettingsEvent } from "../lib/types";
import type { AirControlClient, ConnectionState } from "../lib/ws";

type ScreenClient = Pick<
  AirControlClient,
  "on" | "onState" | "send" | "request"
>;

export interface SettingsProps {
  client: ScreenClient;
  connectionState: ConnectionState;
}

const SETTING_LABELS: Record<string, string> = {
  camera_index: "Camera index",
  clutch_mode: "Clutch mode",
  gate_thresholds: "Gate thresholds",
  store_db_path: "Database path",
};

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Unknown error";
}

function settingLabel(key: string): string {
  const knownLabel = SETTING_LABELS[key];
  if (knownLabel !== undefined) {
    return knownLabel;
  }
  const words = key.replaceAll("_", " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

function settingValue(value: unknown, fixedNumbers = false): string {
  if (value === null || value === undefined) {
    return "Not set";
  }
  if (typeof value === "number") {
    return fixedNumbers ? value.toFixed(2) : String(value);
  }
  if (typeof value === "string" || typeof value === "boolean") {
    return String(value);
  }
  if (Array.isArray(value)) {
    return value.map((item) => settingValue(item, fixedNumbers)).join(", ");
  }
  if (typeof value === "object") {
    return Object.entries(value)
      .map(
        ([key, nestedValue]) =>
          `${settingLabel(key)}: ${settingValue(nestedValue, fixedNumbers)}`,
      )
      .join(" · ");
  }
  return String(value);
}

export function Settings({ client, connectionState }: SettingsProps) {
  const [settings, setSettings] = useState<SettingsEvent["payload"] | null>(
    null,
  );
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const loadGeneration = useRef(0);
  const confirmDialogRef = useRef<HTMLDialogElement>(null);

  const loadSettings = useCallback(async () => {
    const generation = loadGeneration.current + 1;
    loadGeneration.current = generation;
    setLoading(true);
    setLoadError(null);
    try {
      const event = await client.request("get_settings");
      if (event.type !== "settings") {
        throw new Error(`Expected settings, received ${event.type}`);
      }
      if (loadGeneration.current === generation) {
        setSettings(event.payload);
      }
    } catch (error) {
      if (loadGeneration.current === generation) {
        setLoadError(errorMessage(error));
      }
    } finally {
      if (loadGeneration.current === generation) {
        setLoading(false);
      }
    }
  }, [client]);

  useEffect(() => {
    if (connectionState === "open") {
      void loadSettings();
    } else {
      loadGeneration.current += 1;
      setLoading(false);
    }
    return () => {
      loadGeneration.current += 1;
    };
  }, [connectionState, loadSettings]);

  useEffect(() => {
    const dialog = confirmDialogRef.current;
    if (!confirmOpen || dialog === null || dialog.open) {
      return;
    }
    if (typeof dialog.showModal === "function") {
      dialog.showModal();
    } else {
      dialog.setAttribute("open", "");
    }
  }, [confirmOpen]);

  const closeConfirmation = () => {
    const dialog = confirmDialogRef.current;
    if (dialog !== null && dialog.open && typeof dialog.close === "function") {
      dialog.close();
    } else {
      dialog?.removeAttribute("open");
      setConfirmOpen(false);
    }
  };

  const confirmDeleteEverything = async () => {
    setDeleting(true);
    setDeleteError(null);
    try {
      const event = await client.request("delete_everything");
      if (event.type !== "ack" || !event.ok) {
        const detail = event.type === "ack" ? event.error : event.type;
        throw new Error(detail || "The daemon did not confirm deletion");
      }
      closeConfirmation();
      await loadSettings();
    } catch (error) {
      setDeleteError(errorMessage(error));
    } finally {
      setDeleting(false);
    }
  };

  if (connectionState !== "open") {
    return (
      <>
        <header>
          <h1>Settings</h1>
          <p>Read-only daemon configuration and local-data controls.</p>
        </header>
        <div className="empty" role="status">
          <strong>Daemon not connected</strong> — start AirControl with app.cmd
        </div>
      </>
    );
  }

  const entries = settings === null ? [] : Object.entries(settings);
  const databasePath =
    typeof settings?.store_db_path === "string"
      ? settings.store_db_path
      : "Unavailable";

  return (
    <>
      <header>
        <h1>Settings</h1>
        <p>Read-only daemon configuration and local-data controls.</p>
      </header>

      {loading && settings === null && (
        <div className="empty" role="status">
          Loading settings…
        </div>
      )}
      {loadError !== null && (
        <div className="empty" role="alert">
          <strong>Could not load settings.</strong> {loadError}
        </div>
      )}
      {!loading && loadError === null && entries.length === 0 && (
        <div className="empty">
          <strong>No settings were returned.</strong> The daemon is connected but
          did not provide a settings payload.
        </div>
      )}
      {entries.length > 0 && (
        <dl className="settings-grid panel">
          {entries.map(([key, value]) => {
            const fixedNumbers = key.toLowerCase().includes("threshold");
            const useMono = fixedNumbers || key === "store_db_path";
            return (
              <div key={key}>
                <dt>{settingLabel(key)}</dt>
                <dd className={useMono ? "mono" : undefined}>
                  {settingValue(value, fixedNumbers)}
                </dd>
              </div>
            );
          })}
        </dl>
      )}

      <section className="privacy panel" aria-labelledby="privacy-title">
        <h2 id="privacy-title">Privacy and local data</h2>
        <p>All processing is on-device.</p>
        <p>
          AirControl stores only hand-skeleton coordinates—never video or camera
          frames.
        </p>
        <p>
          Database path: <span className="mono">{databasePath}</span>
        </p>
        <button
          className="btn danger"
          type="button"
          onClick={() => {
            setDeleteError(null);
            setConfirmOpen(true);
          }}
        >
          Delete everything
        </button>
      </section>

      {confirmOpen && (
        <dialog
          ref={confirmDialogRef}
          className="confirm"
          aria-labelledby="delete-everything-title"
          onCancel={() => setConfirmOpen(false)}
          onClose={() => setConfirmOpen(false)}
        >
          <h2 id="delete-everything-title">Delete all AirControl data?</h2>
          <p>
            This permanently removes every custom gesture, exemplar, mapping,
            calibration profile, and statistic from the on-device database.
          </p>
          {deleteError !== null && <p role="alert">{deleteError}</p>}
          <div className="row">
            <button
              className="btn"
              type="button"
              disabled={deleting}
              onClick={closeConfirmation}
            >
              Cancel
            </button>
            <button
              className="btn danger"
              type="button"
              disabled={deleting}
              onClick={() => void confirmDeleteEverything()}
            >
              {deleting ? "Deleting…" : "Yes, delete everything"}
            </button>
          </div>
        </dialog>
      )}
    </>
  );
}

export default Settings;
