import { useCallback, useEffect, useMemo, useState } from "react";

import type { LibraryGesture, ServerEvent } from "../lib/types";
import type { AirControlClient, ConnectionState } from "../lib/ws";

type ScreenClient = Pick<
  AirControlClient,
  "on" | "onState" | "send" | "request"
>;

export interface MappingsProps {
  client: ScreenClient;
  connectionState: ConnectionState;
}

interface Verb {
  label: string;
  action: Record<string, unknown>;
  risky?: boolean;
}

interface VerbCategory {
  name: string;
  verbs: readonly Verb[];
}

const VERB_CATEGORIES: readonly VerbCategory[] = [
  {
    name: "Media",
    verbs: [
      { label: "Play/Pause", action: { kind: "hotkey", keys: [179] } },
      { label: "Next track", action: { kind: "hotkey", keys: [176] } },
      { label: "Previous track", action: { kind: "hotkey", keys: [177] } },
      { label: "Volume up", action: { kind: "hotkey", keys: [175] } },
      { label: "Volume down", action: { kind: "hotkey", keys: [174] } },
      { label: "Mute", action: { kind: "hotkey", keys: [173] } },
    ],
  },
  {
    name: "Windows",
    verbs: [
      { label: "Next app", action: { kind: "switch_next" } },
      { label: "Previous app", action: { kind: "switch_previous" } },
      { label: "Task view", action: { kind: "task_view" } },
      { label: "Show desktop", action: { kind: "show_desktop" } },
    ],
  },
  {
    name: "Reading",
    verbs: [
      { label: "Scroll up", action: { kind: "scroll", amount: 3 } },
      { label: "Scroll down", action: { kind: "scroll", amount: -3 } },
      { label: "Page up", action: { kind: "hotkey", keys: [33] } },
      { label: "Page down", action: { kind: "hotkey", keys: [34] } },
    ],
  },
  {
    name: "Browser",
    verbs: [
      { label: "Back", action: { kind: "hotkey", keys: [18, 37] } },
      { label: "Forward", action: { kind: "hotkey", keys: [18, 39] } },
      { label: "Refresh", action: { kind: "hotkey", keys: [116] } },
      { label: "New tab", action: { kind: "hotkey", keys: [17, 84] } },
      {
        label: "Close tab",
        action: { kind: "hotkey", keys: [17, 87] },
        risky: true,
      },
    ],
  },
  {
    name: "Presentation",
    verbs: [
      { label: "Next slide", action: { kind: "hotkey", keys: [34] } },
      { label: "Previous slide", action: { kind: "hotkey", keys: [33] } },
      { label: "Black screen", action: { kind: "hotkey", keys: [66] } },
    ],
  },
  {
    name: "System",
    verbs: [
      { label: "Screenshot", action: { kind: "hotkey", keys: [91, 44] } },
    ],
  },
];

const VK_LABELS: Readonly<Record<number, string>> = {
  9: "Tab",
  13: "Enter",
  16: "Shift",
  17: "Ctrl",
  18: "Alt",
  27: "Esc",
  32: "Space",
  33: "Page Up",
  34: "Page Down",
  37: "Left",
  38: "Up",
  39: "Right",
  40: "Down",
  44: "Print Screen",
  91: "Win",
  116: "F5",
  173: "Mute",
  174: "Volume Down",
  175: "Volume Up",
  176: "Next Track",
  177: "Previous Track",
  179: "Play/Pause",
};

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "An unexpected error occurred";
}

function requireLibrary(event: ServerEvent): LibraryGesture[] {
  if (event.type === "library") {
    return event.gestures;
  }
  if (event.type === "ack" && !event.ok) {
    throw new Error(event.error || "The library is unavailable");
  }
  throw new Error("The daemon returned an unexpected library response");
}

function requireAck(event: ServerEvent): void {
  if (event.type === "ack" && event.ok) {
    return;
  }
  if (event.type === "ack") {
    throw new Error(event.error || "The mapping could not be saved");
  }
  throw new Error("The daemon returned an unexpected mapping response");
}

function sameAction(
  first: Record<string, unknown>,
  second: Record<string, unknown>,
): boolean {
  return JSON.stringify(first) === JSON.stringify(second);
}

function vkLabel(vk: number): string {
  if (vk >= 65 && vk <= 90) {
    return String.fromCharCode(vk);
  }
  if (vk >= 48 && vk <= 57) {
    return String.fromCharCode(vk);
  }
  if (vk >= 112 && vk <= 123) {
    return `F${vk - 111}`;
  }
  return VK_LABELS[vk] ?? `VK ${vk}`;
}

function formatKeys(keys: readonly number[]): string {
  return keys.map(vkLabel).join(" + ");
}

function describeAction(action: Record<string, unknown> | null): string {
  if (action === null) {
    return "Not assigned";
  }

  for (const category of VERB_CATEGORIES) {
    const match = category.verbs.find((verb) => sameAction(verb.action, action));
    if (match !== undefined) {
      return match.label;
    }
  }

  if (
    action.kind === "hotkey" &&
    Array.isArray(action.keys) &&
    action.keys.every((key) => typeof key === "number")
  ) {
    return formatKeys(action.keys);
  }

  if (typeof action.kind === "string") {
    return action.kind.replaceAll("_", " ");
  }
  return "Custom action";
}

function mainKeyVirtualKey(key: string): number | null {
  if (/^[a-z]$/i.test(key)) {
    return key.toUpperCase().charCodeAt(0);
  }
  if (/^[0-9]$/.test(key)) {
    return key.charCodeAt(0);
  }

  const functionKey = /^F([1-9]|1[0-2])$/i.exec(key);
  if (functionKey !== null) {
    return 111 + Number(functionKey[1]);
  }

  const namedKeys: Readonly<Record<string, number>> = {
    ArrowLeft: 37,
    ArrowUp: 38,
    ArrowRight: 39,
    ArrowDown: 40,
    " ": 32,
    Space: 32,
    Spacebar: 32,
    Enter: 13,
    Tab: 9,
    Escape: 27,
    Esc: 27,
  };
  return namedKeys[key] ?? null;
}

function capturedShortcut(event: React.KeyboardEvent<HTMLInputElement>): number[] | null {
  const mainKey = mainKeyVirtualKey(event.key);
  if (mainKey === null) {
    return null;
  }

  const keys: number[] = [];
  if (event.ctrlKey) keys.push(17);
  if (event.altKey) keys.push(18);
  if (event.shiftKey) keys.push(16);
  if (event.metaKey) keys.push(91);
  keys.push(mainKey);
  return keys;
}

export function Mappings({ client, connectionState }: MappingsProps) {
  const [gestures, setGestures] = useState<LibraryGesture[] | null>(null);
  const [selectedGestureId, setSelectedGestureId] = useState<number | null>(null);
  const [shortcutKeys, setShortcutKeys] = useState<number[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [updating, setUpdating] = useState(false);

  const refreshLibrary = useCallback(async () => {
    const event = await client.request("list_library");
    const nextGestures = requireLibrary(event);
    setGestures(nextGestures);
    setSelectedGestureId((current) =>
      current !== null && nextGestures.some((gesture) => gesture.id === current)
        ? current
        : null,
    );
  }, [client]);

  useEffect(() => {
    if (connectionState !== "open") {
      return;
    }

    let active = true;
    setError(null);
    setGestures(null);
    client
      .request("list_library")
      .then((event) => {
        if (!active) return;
        const nextGestures = requireLibrary(event);
        setGestures(nextGestures);
        setSelectedGestureId((current) =>
          current !== null &&
          nextGestures.some((gesture) => gesture.id === current)
            ? current
            : null,
        );
      })
      .catch((requestError: unknown) => {
        if (active) setError(errorMessage(requestError));
      });

    return () => {
      active = false;
    };
  }, [client, connectionState]);

  const selectedGesture = useMemo(
    () =>
      gestures?.find((gesture) => gesture.id === selectedGestureId) ?? null,
    [gestures, selectedGestureId],
  );

  const assignAction = async (action: Record<string, unknown>) => {
    if (selectedGestureId === null || connectionState !== "open") {
      return;
    }

    setUpdating(true);
    setError(null);
    try {
      const reply = await client.request("set_mapping", {
        gesture_id: selectedGestureId,
        action,
      });
      requireAck(reply);
      await refreshLibrary();
    } catch (requestError: unknown) {
      setError(errorMessage(requestError));
    } finally {
      setUpdating(false);
    }
  };

  const handleShortcutKeyDown = (
    event: React.KeyboardEvent<HTMLInputElement>,
  ) => {
    event.preventDefault();
    const keys = capturedShortcut(event);
    if (keys !== null) {
      setShortcutKeys(keys);
    }
  };

  const controlsDisabled =
    selectedGesture === null || updating || connectionState !== "open";

  return (
    <>
      <header>
        <h1>Mappings</h1>
        <p>Choose a deliberate action for each custom gesture.</p>
      </header>

      {connectionState !== "open" ? (
        <div className="empty" role="status">
          <strong>Daemon not connected</strong> — start AirControl with app.cmd
        </div>
      ) : gestures === null && error === null ? (
        <div className="empty" role="status" aria-live="polite">
          <strong>Loading mappings…</strong>
        </div>
      ) : gestures === null ? (
        <div className="empty" role="alert">
          <strong>Mappings could not be loaded.</strong> {error}
        </div>
      ) : (
        <>
          {error !== null && (
            <div className="empty" role="alert">
              <strong>Mapping could not be updated.</strong> {error}
            </div>
          )}

          {gestures.length === 0 && (
            <div className="empty">
              <strong>No custom gestures yet.</strong> Run record.cmd to record
              a gesture before assigning an action.
            </div>
          )}

          <div className="map-layout">
            <section className="map-gestures" aria-label="Custom gestures">
              {gestures.map((gesture) => (
                <button
                  key={gesture.id}
                  type="button"
                  aria-pressed={gesture.id === selectedGestureId}
                  onClick={() => setSelectedGestureId(gesture.id)}
                >
                  {gesture.name}
                  <span className="current">
                    {describeAction(gesture.mapping)}
                  </span>
                </button>
              ))}
            </section>

            <section className="map-catalog" aria-label="Action catalog">
              {selectedGesture === null && gestures.length > 0 && (
                <div className="empty" role="status">
                  <strong>Select a gesture</strong> to assign an action.
                </div>
              )}

              {VERB_CATEGORIES.map((category) => (
                <section key={category.name}>
                  <h3>{category.name}</h3>
                  <div className="verb-row">
                    {category.verbs.map((verb) => (
                      <button
                        key={verb.label}
                        type="button"
                        disabled={controlsDisabled}
                        data-risky={verb.risky ? "true" : undefined}
                        onClick={() => void assignAction(verb.action)}
                      >
                        {verb.label}
                      </button>
                    ))}
                  </div>
                </section>
              ))}

              <section className="shortcut-recorder">
                <h3>Any shortcut</h3>
                <p>This reaches any shortcut in any app.</p>
                <input
                  type="text"
                  readOnly
                  aria-label="Keyboard shortcut"
                  placeholder="Focus here, then press a shortcut"
                  value={formatKeys(shortcutKeys)}
                  onKeyDown={handleShortcutKeyDown}
                />{" "}
                <button
                  className="btn primary"
                  type="button"
                  disabled={controlsDisabled || shortcutKeys.length === 0}
                  onClick={() =>
                    void assignAction({ kind: "hotkey", keys: shortcutKeys })
                  }
                >
                  {updating ? "Assigning…" : "Assign"}
                </button>
              </section>
            </section>
          </div>
        </>
      )}
    </>
  );
}

export default Mappings;
