export interface ActionOption {
  label: string;
  action: Record<string, unknown>;
}

export interface ActionGroup {
  label: string;
  options: readonly ActionOption[];
}

export const ACTION_GROUPS: readonly ActionGroup[] = [
  {
    label: "Media",
    options: [
      { label: "Play / pause", action: { kind: "hotkey", keys: [179] } },
      { label: "Next track", action: { kind: "hotkey", keys: [176] } },
      { label: "Previous track", action: { kind: "hotkey", keys: [177] } },
      { label: "Volume up", action: { kind: "hotkey", keys: [175] } },
      { label: "Volume down", action: { kind: "hotkey", keys: [174] } },
      { label: "Mute", action: { kind: "hotkey", keys: [173] } },
    ],
  },
  {
    label: "Windows",
    options: [
      { label: "Next app", action: { kind: "switch_next" } },
      { label: "Previous app", action: { kind: "switch_previous" } },
      { label: "Task view", action: { kind: "task_view" } },
      { label: "Show desktop", action: { kind: "show_desktop" } },
      { label: "Screenshot", action: { kind: "hotkey", keys: [91, 44] } },
    ],
  },
  {
    label: "Reading and browser",
    options: [
      { label: "Scroll up", action: { kind: "scroll", amount: 3 } },
      { label: "Scroll down", action: { kind: "scroll", amount: -3 } },
      { label: "Page up", action: { kind: "hotkey", keys: [33] } },
      { label: "Page down", action: { kind: "hotkey", keys: [34] } },
      { label: "Browser back", action: { kind: "hotkey", keys: [18, 37] } },
      { label: "Browser forward", action: { kind: "hotkey", keys: [18, 39] } },
      { label: "Refresh", action: { kind: "hotkey", keys: [116] } },
      { label: "New tab", action: { kind: "hotkey", keys: [17, 84] } },
      { label: "Close tab", action: { kind: "hotkey", keys: [17, 87] } },
    ],
  },
] as const;

const VK_LABELS: Readonly<Record<number, string>> = {
  17: "Ctrl",
  18: "Alt",
  33: "Page Up",
  34: "Page Down",
  37: "Left",
  39: "Right",
  44: "Print Screen",
  84: "T",
  87: "W",
  91: "Win",
  116: "F5",
  173: "Mute",
  174: "Volume Down",
  175: "Volume Up",
  176: "Next Track",
  177: "Previous Track",
  179: "Play / Pause",
};

export function actionKey(action: Record<string, unknown>): string {
  return JSON.stringify(action);
}

export function describeAction(
  action: Record<string, unknown> | null,
): string {
  if (action === null) {
    return "Not assigned";
  }

  const serialized = actionKey(action);
  for (const group of ACTION_GROUPS) {
    const match = group.options.find(
      (option) => actionKey(option.action) === serialized,
    );
    if (match !== undefined) {
      return match.label;
    }
  }

  if (
    action.kind === "hotkey" &&
    Array.isArray(action.keys) &&
    action.keys.every((key) => typeof key === "number")
  ) {
    return action.keys
      .map((key) => VK_LABELS[key] ?? `VK ${key}`)
      .join(" + ");
  }

  if (typeof action.kind === "string") {
    const words = action.kind.replaceAll("_", " ");
    return words.charAt(0).toUpperCase() + words.slice(1);
  }
  return "Custom action";
}
