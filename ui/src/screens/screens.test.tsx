import { StrictMode, act, type ReactElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AppSettingsProvider } from "../lib/app-settings";
import { actionKey, describeAction } from "../lib/actions";
import type {
  CommandFields,
  CommandName,
  LibraryEvent,
  RecordingEvent,
  ServerEvent,
  ServerEventType,
  SettingsEvent,
} from "../lib/types";
import type { ConnectionState } from "../lib/ws";
import { About } from "./About";
import { Appearance } from "./Appearance";
import { Calibration } from "./Calibration";
import { Dashboard } from "./Dashboard";
import { Gestures } from "./Gestures";
import { Settings } from "./Settings";

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });

const appSettingsEvent: ServerEvent = {
  v: 1,
  type: "app_settings",
  settings: {
    sensitivity: 50,
    smoothing: 64,
    cursor_speed: 1.25,
    pointer_responsiveness: 35,
    click_engage: 0.45,
    click_release: 0.6,
    pinch_approach: 0.75,
    pinch_drag_release: 0.08,
    arm_hold_seconds: 0.7,
    pause_hold_seconds: 0.55,
    scroll_speed: 50,
    swipe_distance: 0.9,
    dominant_hand: "right",
    click_mode: "single",
    drag_lock_enabled: true,
    theme: "system",
    airy_enabled: true,
  },
};

const resetAppSettingsEvent: ServerEvent = {
  ...appSettingsEvent,
  settings: {
    ...appSettingsEvent.settings,
    cursor_speed: 1,
    pointer_responsiveness: 20,
  },
};

const libraryEvent: LibraryEvent = {
  v: 1,
  type: "library",
  gestures: [
    {
      id: 7,
      name: "Desk wave",
      description: "A deliberate wave",
      exemplar_count: 3,
      confirms: 8,
      rejects: 1,
      threshold_offset: 0.05,
      mapping: { kind: "switch_next", enabled: true },
      animation: null,
    },
  ],
};

const effectiveSettingsEvent: SettingsEvent = {
  v: 1,
  type: "settings",
  payload: {
    clutch_mode: "hold",
    gate_thresholds: { t1: 0.8, t2: 0.15, t3: 0.5 },
    camera_index: 0,
    store_db_path: "C:\\AirControl\\aircontrol.db",
    has_calibration_profile: true,
    camera_state: "active",
    camera_error: null,
    calibration: {
      hand_size: 0.1432,
      lighting_acceptable: true,
      created_at: 1_767_225_600,
    },
  },
};

type StubRequestReply =
  | ServerEvent
  | ((fields: CommandFields) => ServerEvent | Promise<ServerEvent>);

class StubClient {
  readonly sent: Array<{ name: CommandName; fields: CommandFields }> = [];
  readonly requested: Array<{ name: CommandName; fields: CommandFields }> = [];
  private readonly listeners = new Map<
    ServerEventType,
    Set<(event: ServerEvent) => void>
  >();
  private readonly requestReplies = new Map<CommandName, StubRequestReply>();
  private readonly stateCallbacks = new Set<
    (state: ConnectionState) => void
  >();
  private previewCallbacks = new Set<(frame: Blob) => void>();
  private currentAppSettings: ServerEvent = appSettingsEvent;
  private currentRecording: RecordingEvent = recordingEvent({
    phase: "inactive",
    name: "",
  });
  private connectionState: ConnectionState;

  constructor(
    private readonly libraryReply: ServerEvent = libraryEvent,
    private readonly settingsReply: ServerEvent = effectiveSettingsEvent,
    connectionState: ConnectionState = "open",
  ) {
    this.connectionState = connectionState;
  }

  on(
    type: ServerEventType,
    callback: (event: ServerEvent) => void,
  ): () => void {
    const callbacks = this.listeners.get(type) ?? new Set();
    callbacks.add(callback);
    this.listeners.set(type, callbacks);
    if (type === "status") {
      callback({
        v: 1,
        type: "status",
        armed: true,
        raw_pose: "OPEN_PALM",
        active_pose: "OPEN_PALM",
        hold_progress: 0.75,
        hand_visible: true,
        status_text: "Armed and tracking",
      });
    }
    return () => callbacks.delete(callback);
  }

  setRequestReply(name: CommandName, reply: StubRequestReply): void {
    this.requestReplies.set(name, reply);
  }

  emit(event: ServerEvent): void {
    if (event.type === "recording") {
      this.currentRecording = event;
    }
    this.listeners
      .get(event.type)
      ?.forEach((callback) => callback(event));
  }

  send(name: CommandName, fields: CommandFields = {}): void {
    this.sent.push({ name, fields });
  }

  onState(callback: (state: ConnectionState) => void): () => void {
    this.stateCallbacks.add(callback);
    callback(this.connectionState);
    return () => this.stateCallbacks.delete(callback);
  }

  setConnectionState(state: ConnectionState): void {
    this.connectionState = state;
    this.stateCallbacks.forEach((callback) => callback(state));
  }

  onPreviewFrame(callback: (frame: Blob) => void): () => void {
    this.previewCallbacks.add(callback);
    return () => this.previewCallbacks.delete(callback);
  }

  emitPreviewFrame(frame: Blob): void {
    this.previewCallbacks.forEach((callback) => callback(frame));
  }

  request(
    name: CommandName,
    fields: CommandFields = {},
  ): Promise<ServerEvent> {
    this.requested.push({ name, fields });
    const reply = this.requestReplies.get(name);
    if (reply !== undefined) {
      return Promise.resolve(
        typeof reply === "function" ? reply(fields) : reply,
      );
    }
    if (name === "get_app_settings") {
      return Promise.resolve(this.currentAppSettings);
    }
    if (name === "reset_app_settings") {
      this.currentAppSettings = resetAppSettingsEvent;
      return Promise.resolve(resetAppSettingsEvent);
    }
    if (name === "list_library") {
      return Promise.resolve(this.libraryReply);
    }
    if (name === "get_settings") {
      return Promise.resolve(this.settingsReply);
    }
    if (name === "start_recording") {
      this.currentRecording = recordingEvent({
        name:
          typeof fields.gesture_name === "string"
            ? fields.gesture_name.trim()
            : "",
      });
    }
    if (name === "get_recording_state") {
      return Promise.resolve(this.currentRecording);
    }
    return Promise.resolve({ v: 1, type: "ack", ok: true, error: "" });
  }
}

function recordingEvent(
  overrides: Partial<Omit<RecordingEvent, "v" | "type">> = {},
): RecordingEvent {
  return {
    v: 1,
    type: "recording",
    phase: "capturing",
    name: "Desk wave",
    takes_confirmed: 0,
    min_takes: 3,
    max_takes: 5,
    pending_take: false,
    pending_take_frames: null,
    outcome: null,
    ...overrides,
  };
}

function matchMedia(query: string): MediaQueryList {
  return {
    matches: false,
    media: query,
    onchange: null,
    addListener: () => undefined,
    removeListener: () => undefined,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    dispatchEvent: () => true,
  };
}

const roots: Root[] = [];

function withSettings(
  client: StubClient,
  element: ReactElement,
  connectionState: ConnectionState = "open",
): ReactElement {
  return (
    <AppSettingsProvider client={client} connectionState={connectionState}>
      {element}
    </AppSettingsProvider>
  );
}

async function renderScreen(
  element: ReactElement,
): Promise<{ container: HTMLElement; root: Root }> {
  const container = document.createElement("div");
  document.body.append(container);
  const root = createRoot(container);
  roots.push(root);
  await act(async () => {
    root.render(element);
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });
  return { container, root };
}

function normalizedText(element: Element): string {
  return element.textContent?.replace(/\s+/g, " ").trim() ?? "";
}

function buttonByText(root: ParentNode, text: string): HTMLButtonElement {
  const button = Array.from(root.querySelectorAll<HTMLButtonElement>("button")).find(
    (candidate) => normalizedText(candidate) === text,
  );
  if (button === undefined) {
    throw new Error(`Could not find button labelled ${text}`);
  }
  return button;
}

function inputByLabel(root: ParentNode, text: string): HTMLInputElement {
  const label = Array.from(root.querySelectorAll<HTMLLabelElement>("label")).find(
    (candidate) => normalizedText(candidate).includes(text),
  );
  if (label === undefined) {
    throw new Error(`Could not find input label ${text}`);
  }
  const input =
    (label.htmlFor === "" ? label.querySelector("input") : null) ??
    document.getElementById(label.htmlFor);
  if (!(input instanceof HTMLInputElement)) {
    throw new Error(`Label ${text} does not reference an input`);
  }
  return input;
}

async function clickElement(element: HTMLElement): Promise<void> {
  await act(async () => {
    element.click();
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });
}

async function changeInput(input: HTMLInputElement, value: string): Promise<void> {
  await act(async () => {
    const valueSetter = Object.getOwnPropertyDescriptor(
      HTMLInputElement.prototype,
      "value",
    )?.set;
    valueSetter?.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
    await Promise.resolve();
  });
}

async function emitRecording(
  client: StubClient,
  event: RecordingEvent,
): Promise<void> {
  await act(async () => {
    client.emit(event);
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });
}

async function openRecordingPanel(container: HTMLElement): Promise<HTMLDialogElement> {
  await clickElement(buttonByText(container, "+ Add Gesture"));
  const dialog = container.querySelector<HTMLDialogElement>("dialog[open]");
  if (dialog === null) {
    throw new Error("The recording dialog did not open");
  }
  return dialog;
}

async function startRecording(
  container: HTMLElement,
  name = "Desk wave",
): Promise<HTMLDialogElement> {
  const dialog = await openRecordingPanel(container);
  await changeInput(inputByLabel(dialog, "Gesture name"), name);
  await clickElement(buttonByText(dialog, "Start recording"));
  return dialog;
}

function unmountRoot(root: Root): void {
  const index = roots.indexOf(root);
  if (index >= 0) {
    roots.splice(index, 1);
  }
  act(() => root.unmount());
}

beforeEach(() => {
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: matchMedia,
  });
});

afterEach(() => {
  for (const root of roots.splice(0)) {
    act(() => root.unmount());
  }
  document.body.replaceChildren();
  document.documentElement.removeAttribute("data-theme");
  document.documentElement.style.removeProperty("color-scheme");
  vi.restoreAllMocks();
});

describe("application screens", () => {
  it("compares and describes mapped actions without enabled metadata", () => {
    const mappedAction = { kind: "switch_next", enabled: false };

    expect(actionKey(mappedAction)).toBe(actionKey({ kind: "switch_next" }));
    expect(describeAction(mappedAction)).toBe("Next app");
  });

  it("renders live Dashboard controls and releases every preview URL", async () => {
    let nextObjectUrl = 1;
    const createObjectURL = vi.fn(
      () => `blob:dashboard-preview-${nextObjectUrl++}`,
    );
    const revokeObjectURL = vi.fn();
    Object.defineProperties(URL, {
      createObjectURL: { configurable: true, value: createObjectURL },
      revokeObjectURL: { configurable: true, value: revokeObjectURL },
    });

    const client = new StubClient();
    const { container, root } = await renderScreen(
      withSettings(
        client,
        <Dashboard
          client={client}
          connectionState="open"
          onNavigate={() => undefined}
        />,
      ),
    );

    const armedSwitch = container.querySelector<HTMLElement>(
      '[role="switch"][aria-labelledby="dashboard-armed-label"]',
    );
    expect(armedSwitch?.getAttribute("aria-checked")).toBe("true");
    expect(container.textContent).toContain("ARMED");
    expect(container.textContent).toContain("Sensitivity");
    expect(container.textContent).toContain("Smoothing");
    expect(
      client.sent.filter(({ name }) => name === "set_preview").at(-1),
    ).toEqual({ name: "set_preview", fields: { enabled: true } });

    await act(async () => {
      client.emitPreviewFrame(new Blob(["first"], { type: "image/jpeg" }));
    });
    const image = container.querySelector<HTMLImageElement>(".camera-hero-frame img");
    expect(image?.getAttribute("src")).toBe("blob:dashboard-preview-1");
    expect(container.textContent).toContain("LIVE");

    await act(async () => {
      client.emitPreviewFrame(new Blob(["second"], { type: "image/jpeg" }));
    });
    expect(image?.getAttribute("src")).toBe("blob:dashboard-preview-2");
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:dashboard-preview-1");

    unmountRoot(root);
    expect(
      client.sent.filter(({ name }) => name === "set_preview").at(-1),
    ).toEqual({ name: "set_preview", fields: { enabled: false } });
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:dashboard-preview-2");
  });

  it("renders a custom gesture with its human-readable mapping", async () => {
    const client = new StubClient();
    const { container } = await renderScreen(
      withSettings(
        client,
        <Gestures client={client} connectionState="open" />,
      ),
    );

    expect(container.textContent).toContain("Desk wave");
    expect(container.textContent).toContain("Next app");
    expect(
      container.querySelector<HTMLSelectElement>("#gesture-action-7")?.value,
    ).toBe(actionKey({ kind: "switch_next" }));
  });

  it("opens recording setup and starts with the entered gesture name", async () => {
    const client = new StubClient();
    const { container } = await renderScreen(
      withSettings(
        client,
        <Gestures client={client} connectionState="open" />,
      ),
    );

    const dialog = await openRecordingPanel(container);
    expect(normalizedText(dialog)).toContain("Gesture name");
    await changeInput(inputByLabel(dialog, "Gesture name"), "Window circle");
    await clickElement(buttonByText(dialog, "Start recording"));

    expect(
      client.requested.filter(({ name }) => name === "start_recording").at(-1),
    ).toEqual({
      name: "start_recording",
      fields: { gesture_name: "Window circle" },
    });
    expect(document.activeElement).toBe(
      dialog.querySelector("#record-gesture-title"),
    );
  });

  it("keeps recording setup open when calibration is required", async () => {
    const client = new StubClient();
    const navigation = document.createElement("nav");
    navigation.className = "sidebar";
    navigation.setAttribute("aria-label", "Primary navigation");
    const calibrationButton = document.createElement("button");
    calibrationButton.type = "button";
    calibrationButton.textContent = "Calibration";
    const navigationClick = vi.fn();
    calibrationButton.addEventListener("click", navigationClick);
    navigation.append(calibrationButton);
    document.body.append(navigation);
    client.setRequestReply("start_recording", {
      v: 1,
      type: "ack",
      ok: false,
      error: "calibrate first",
    });
    const { container } = await renderScreen(
      withSettings(
        client,
        <Gestures client={client} connectionState="open" />,
      ),
    );

    const dialog = await startRecording(container);

    expect(normalizedText(dialog)).toContain(
      "Run calibration first from the Calibration screen",
    );
    expect(normalizedText(dialog)).not.toContain(
      "Perform the gesture, then pause.",
    );
    expect(container.querySelector("dialog[open]")).toBe(dialog);
    expect(navigation.hasAttribute("inert")).toBe(true);
    expect(
      Array.from(dialog.querySelectorAll<HTMLElement>("a, button")).some(
        (element) => normalizedText(element).includes("Calibration"),
      ),
    ).toBe(true);

    await clickElement(buttonByText(dialog, "Go to Calibration"));
    expect(navigationClick).toHaveBeenCalledTimes(1);
    expect(document.activeElement).toBe(calibrationButton);
    expect(navigation.hasAttribute("inert")).toBe(false);
    expect(container.querySelector("dialog[open]")).toBeNull();
  });

  it("ignores stray recording events when start is refused", async () => {
    const client = new StubClient();
    let resolveStart: ((event: ServerEvent) => void) | null = null;
    client.setRequestReply(
      "start_recording",
      () =>
        new Promise<ServerEvent>((resolve) => {
          resolveStart = resolve;
        }),
    );
    const { container } = await renderScreen(
      withSettings(
        client,
        <Gestures client={client} connectionState="open" />,
      ),
    );
    const dialog = await openRecordingPanel(container);
    await changeInput(inputByLabel(dialog, "Gesture name"), "New motion");
    await clickElement(buttonByText(dialog, "Start recording"));

    await emitRecording(
      client,
      recordingEvent({
        phase: "pending_take",
        name: "Older session",
        pending_take: true,
        pending_take_frames: 18,
      }),
    );
    expect(normalizedText(dialog)).not.toContain("Take captured");

    await act(async () => {
      resolveStart?.({
        v: 1,
        type: "ack",
        ok: false,
        error: "recording already active",
      });
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(normalizedText(dialog)).toContain(
      "Recording could not start: recording already active.",
    );
    expect(normalizedText(dialog)).not.toContain(
      "Perform the gesture, then pause.",
    );
  });

  it("reconciles a start request whose acknowledgement was lost", async () => {
    const client = new StubClient();
    client.setRequestReply(
      "start_recording",
      () => new Promise<ServerEvent>(() => undefined),
    );
    client.setRequestReply(
      "get_recording_state",
      recordingEvent({ name: "Recovered motion" }),
    );
    const { container, root } = await renderScreen(
      withSettings(
        client,
        <Gestures client={client} connectionState="open" />,
      ),
    );
    const dialog = await openRecordingPanel(container);
    await changeInput(inputByLabel(dialog, "Gesture name"), "Recovered motion");
    await clickElement(buttonByText(dialog, "Start recording"));

    client.setConnectionState("closed");
    await act(async () => {
      root.render(
        withSettings(
          client,
          <Gestures client={client} connectionState="closed" />,
          "closed",
        ),
      );
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(normalizedText(dialog)).toContain(
      "The connection was lost while recording started.",
    );

    client.setConnectionState("open");
    await act(async () => {
      root.render(
        withSettings(
          client,
          <Gestures client={client} connectionState="open" />,
        ),
      );
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(normalizedText(dialog)).toContain("Recovered motion");
    expect(normalizedText(dialog)).toContain("Perform the gesture, then pause.");
    expect(
      client.requested.filter(({ name }) => name === "get_recording_state"),
    ).toHaveLength(1);
  });

  it("drives pending take controls and save readiness from recording events", async () => {
    const client = new StubClient();
    const { container } = await renderScreen(
      withSettings(
        client,
        <Gestures client={client} connectionState="open" />,
      ),
    );
    const dialog = await startRecording(container);

    await emitRecording(
      client,
      recordingEvent({ takes_confirmed: 2 }),
    );
    expect(normalizedText(dialog)).toContain("Perform the gesture, then pause.");
    expect(normalizedText(dialog)).toContain("Keep 2 of 3–5 takes.");
    expect(buttonByText(dialog, "Save gesture").disabled).toBe(true);

    await emitRecording(
      client,
      recordingEvent({
        phase: "pending_take",
        takes_confirmed: 2,
        pending_take: true,
        pending_take_frames: 24,
      }),
    );
    expect(normalizedText(dialog)).toContain("Take captured (24 frames)");
    expect(normalizedText(document.activeElement ?? document.body)).toContain(
      "Take captured (24 frames)",
    );
    expect(buttonByText(dialog, "Keep")).toBeInstanceOf(HTMLButtonElement);
    expect(buttonByText(dialog, "Discard")).toBeInstanceOf(HTMLButtonElement);

    await clickElement(buttonByText(dialog, "Discard"));
    expect(client.sent.at(-1)).toEqual({ name: "discard_take", fields: {} });

    await emitRecording(client, recordingEvent({ takes_confirmed: 2 }));
    expect(document.activeElement).toBe(
      dialog.querySelector("#record-gesture-title"),
    );
    await emitRecording(
      client,
      recordingEvent({
        phase: "pending_take",
        takes_confirmed: 2,
        pending_take: true,
        pending_take_frames: 31,
      }),
    );
    await clickElement(buttonByText(dialog, "Keep"));
    expect(client.sent.at(-1)).toEqual({ name: "confirm_take", fields: {} });
    expect(buttonByText(dialog, "Save gesture").disabled).toBe(true);

    await emitRecording(client, recordingEvent({ takes_confirmed: 3 }));
    expect(buttonByText(dialog, "Save gesture").disabled).toBe(false);
  });

  it("shows a live capture status line and updates it as capture_state changes", async () => {
    const client = new StubClient();
    const { container } = await renderScreen(
      withSettings(
        client,
        <Gestures client={client} connectionState="open" />,
      ),
    );
    const dialog = await startRecording(container);

    await emitRecording(
      client,
      recordingEvent({ takes_confirmed: 0, capture_state: "searching" }),
    );
    expect(normalizedText(dialog)).toContain("Looking for your hand");

    await emitRecording(
      client,
      recordingEvent({ takes_confirmed: 0, capture_state: "hand_present" }),
    );
    expect(normalizedText(dialog)).toContain("Hand detected");
    expect(normalizedText(dialog)).not.toContain("Looking for your hand");

    await emitRecording(
      client,
      recordingEvent({ takes_confirmed: 0, capture_state: "in_motion" }),
    );
    expect(normalizedText(dialog)).toContain("Motion detected");

    await emitRecording(
      client,
      recordingEvent({
        phase: "pending_take",
        takes_confirmed: 0,
        pending_take: true,
        pending_take_frames: 14,
        capture_state: "pending_take",
      }),
    );
    expect(normalizedText(dialog)).toContain("Take captured — keep or discard");
  });

  it("closes after a saved recording and refreshes the library", async () => {
    const client = new StubClient();
    const { container } = await renderScreen(
      withSettings(
        client,
        <Gestures client={client} connectionState="open" />,
      ),
    );
    expect(
      client.requested.filter(({ name }) => name === "list_library"),
    ).toHaveLength(1);
    const dialog = await startRecording(container);
    await emitRecording(client, recordingEvent({ takes_confirmed: 3 }));

    await clickElement(buttonByText(dialog, "Save gesture"));
    expect(
      client.requested.filter(({ name }) => name === "finish_recording").at(-1),
    ).toEqual({ name: "finish_recording", fields: {} });

    await emitRecording(
      client,
      recordingEvent({
        phase: "saved",
        takes_confirmed: 3,
        outcome: {
          saved: true,
          reason: "saved",
          gesture_id: 8,
          conflict_gesture_name: null,
        },
      }),
    );

    expect(container.querySelector("dialog[open]")).toBeNull();
    expect(
      client.requested.filter(({ name }) => name === "list_library"),
    ).toHaveLength(2);
    expect(
      client.sent.filter(({ name }) => name === "cancel_recording"),
    ).toHaveLength(0);
  });

  it("guides the user from a saved recording straight into mapping it", async () => {
    const withNewGesture: LibraryEvent = {
      ...libraryEvent,
      gestures: [
        ...libraryEvent.gestures,
        {
          id: 42,
          name: "Window circle",
          description: "",
          exemplar_count: 3,
          confirms: 3,
          rejects: 0,
          threshold_offset: 0,
          mapping: null,
          animation: null,
        },
      ],
    };
    const withMappedNewGesture: LibraryEvent = {
      ...withNewGesture,
      gestures: withNewGesture.gestures.map((gesture) =>
        gesture.id === 42
          ? { ...gesture, mapping: { kind: "switch_previous", enabled: true } }
          : gesture,
      ),
    };

    let libraryCallCount = 0;
    const client = new StubClient();
    client.setRequestReply("list_library", () => {
      libraryCallCount += 1;
      if (libraryCallCount === 1) {
        return libraryEvent;
      }
      if (libraryCallCount === 2) {
        return withNewGesture;
      }
      return withMappedNewGesture;
    });

    const scrollIntoView = vi.fn();
    const previousScrollIntoView = HTMLElement.prototype.scrollIntoView;
    HTMLElement.prototype.scrollIntoView = scrollIntoView;

    try {
      const { container } = await renderScreen(
        withSettings(
          client,
          <Gestures client={client} connectionState="open" />,
        ),
      );

      const dialog = await startRecording(container, "Window circle");
      await emitRecording(
        client,
        recordingEvent({ name: "Window circle", takes_confirmed: 3 }),
      );
      await clickElement(buttonByText(dialog, "Save gesture"));
      await emitRecording(
        client,
        recordingEvent({
          phase: "saved",
          name: "Window circle",
          takes_confirmed: 3,
          outcome: {
            saved: true,
            reason: "saved",
            gesture_id: 42,
            conflict_gesture_name: null,
          },
        }),
      );

      expect(container.querySelector("dialog[open]")).toBeNull();

      const card = container.querySelector<HTMLElement>(
        '[data-needs-mapping="true"]',
      );
      expect(card).not.toBeNull();
      const cardText = normalizedText(card as HTMLElement);
      expect(cardText).toContain("Now choose what");
      expect(cardText).toContain("Window circle");
      expect(cardText).toContain("won’t do anything until you map it.");

      const select = container.querySelector<HTMLSelectElement>(
        "#gesture-action-42",
      );
      expect(select).not.toBeNull();
      expect(document.activeElement).toBe(select);
      expect(select?.getAttribute("aria-describedby")).toBe(
        "gesture-map-prompt-42",
      );
      expect(scrollIntoView).toHaveBeenCalled();

      await act(async () => {
        if (select !== null) {
          select.value = actionKey({ kind: "switch_previous" });
          select.dispatchEvent(new Event("change", { bubbles: true }));
        }
        await Promise.resolve();
        await Promise.resolve();
        await Promise.resolve();
      });

      expect(
        client.requested.filter(({ name }) => name === "set_mapping").at(-1),
      ).toEqual({
        name: "set_mapping",
        fields: {
          gesture_id: 42,
          action: { kind: "switch_previous" },
          enabled: true,
        },
      });

      expect(
        container.querySelector('[data-needs-mapping="true"]'),
      ).toBeNull();
      expect(container.textContent).not.toContain("Now choose what");
    } finally {
      HTMLElement.prototype.scrollIntoView = previousScrollIntoView;
    }
  });

  it("shows a friendly conflict reason and lets the user try again", async () => {
    const client = new StubClient();
    const { container } = await renderScreen(
      withSettings(
        client,
        <Gestures client={client} connectionState="open" />,
      ),
    );
    const dialog = await startRecording(container);

    await emitRecording(
      client,
      recordingEvent({
        phase: "refused",
        takes_confirmed: 3,
        outcome: {
          saved: false,
          reason: "too similar",
          gesture_id: null,
          conflict_gesture_name: "Existing wave",
        },
      }),
    );

    expect(normalizedText(dialog)).toContain("Too similar to Existing wave");
    expect(document.activeElement).toBe(
      dialog.querySelector("#record-gesture-title"),
    );
    await clickElement(buttonByText(dialog, "Try again"));
    expect(normalizedText(dialog)).not.toContain("Too similar to Existing wave");
    expect(inputByLabel(dialog, "Gesture name")).toBeInstanceOf(HTMLInputElement);
    expect(buttonByText(dialog, "Start recording")).toBeInstanceOf(
      HTMLButtonElement,
    );
  });

  it("cancels an active recording from the Cancel button", async () => {
    const client = new StubClient();
    const { container } = await renderScreen(
      withSettings(
        client,
        <Gestures client={client} connectionState="open" />,
      ),
    );
    const dialog = await startRecording(container);
    await emitRecording(client, recordingEvent());

    await clickElement(buttonByText(dialog, "Cancel"));

    expect(
      client.sent.filter(({ name }) => name === "cancel_recording"),
    ).toEqual([{ name: "cancel_recording", fields: {} }]);
    expect(container.querySelector("dialog[open]")).toBeNull();
  });

  it("delivers a queued cancel after the daemon reconnects", async () => {
    const client = new StubClient();
    const { container, root } = await renderScreen(
      withSettings(
        client,
        <Gestures client={client} connectionState="open" />,
      ),
    );
    const dialog = await startRecording(container);
    await emitRecording(client, recordingEvent());

    client.setConnectionState("closed");
    await act(async () => {
      root.render(
        withSettings(
          client,
          <Gestures client={client} connectionState="closed" />,
          "closed",
        ),
      );
      await Promise.resolve();
      await Promise.resolve();
    });
    await clickElement(buttonByText(dialog, "Cancel"));
    expect(
      client.sent.filter(({ name }) => name === "cancel_recording"),
    ).toHaveLength(0);

    await act(async () => {
      client.setConnectionState("open");
      await Promise.resolve();
    });
    expect(
      client.sent.filter(({ name }) => name === "cancel_recording"),
    ).toEqual([{ name: "cancel_recording", fields: {} }]);
  });

  it("keeps Start disabled while the daemon is disconnected", async () => {
    const client = new StubClient();
    const { container } = await renderScreen(
      withSettings(
        client,
        <Gestures client={client} connectionState="closed" />,
        "closed",
      ),
    );
    const dialog = await openRecordingPanel(container);
    await changeInput(inputByLabel(dialog, "Gesture name"), "Window circle");

    const startButton = buttonByText(dialog, "Start recording");
    expect(startButton.disabled).toBe(true);
    expect(normalizedText(dialog)).toContain("Daemon not connected");
    await clickElement(startButton);
    expect(
      client.requested.filter(({ name }) => name === "start_recording"),
    ).toHaveLength(0);
  });

  it("cancels an active recording when Escape closes the dialog", async () => {
    const client = new StubClient();
    const { container } = await renderScreen(
      withSettings(
        client,
        <StrictMode>
          <Gestures client={client} connectionState="open" />
        </StrictMode>,
      ),
    );
    const dialog = await startRecording(container);
    await emitRecording(client, recordingEvent());

    await act(async () => {
      dialog.dispatchEvent(
        new Event("cancel", { bubbles: true, cancelable: true }),
      );
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(
      client.sent.filter(({ name }) => name === "cancel_recording"),
    ).toEqual([{ name: "cancel_recording", fields: {} }]);
    expect(container.querySelector("dialog[open]")).toBeNull();
  });

  it("releases recording preview URLs when frames change and the panel closes", async () => {
    let nextObjectUrl = 1;
    const createObjectURL = vi.fn(
      () => `blob:recording-preview-${nextObjectUrl++}`,
    );
    const revokeObjectURL = vi.fn();
    Object.defineProperties(URL, {
      createObjectURL: { configurable: true, value: createObjectURL },
      revokeObjectURL: { configurable: true, value: revokeObjectURL },
    });

    const client = new StubClient();
    const { container } = await renderScreen(
      withSettings(
        client,
        <Gestures client={client} connectionState="open" />,
      ),
    );
    const dialog = await startRecording(container);
    await emitRecording(client, recordingEvent());

    await act(async () => {
      client.emitPreviewFrame(new Blob(["first"], { type: "image/jpeg" }));
      await Promise.resolve();
    });
    const image = dialog.querySelector<HTMLImageElement>(".recording-preview img");
    expect(image?.getAttribute("src")).toBe("blob:recording-preview-1");

    await act(async () => {
      client.emitPreviewFrame(new Blob(["second"], { type: "image/jpeg" }));
      await Promise.resolve();
    });
    expect(image?.getAttribute("src")).toBe("blob:recording-preview-2");
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:recording-preview-1");

    await clickElement(buttonByText(dialog, "Cancel"));
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:recording-preview-2");
  });

  it("cancels an active recording when the dialog backdrop is clicked", async () => {
    const client = new StubClient();
    const { container } = await renderScreen(
      withSettings(
        client,
        <Gestures client={client} connectionState="open" />,
      ),
    );
    await startRecording(container);
    await emitRecording(client, recordingEvent());

    await act(async () => {
      document.body.dispatchEvent(
        new Event("pointerdown", { bubbles: true, cancelable: true }),
      );
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(
      client.sent.filter(({ name }) => name === "cancel_recording"),
    ).toEqual([{ name: "cancel_recording", fields: {} }]);
    expect(container.querySelector("dialog[open]")).toBeNull();
  });

  it("preserves mapping state when changing actions and toggles it accessibly", async () => {
    const disabledLibrary: LibraryEvent = {
      ...libraryEvent,
      gestures: libraryEvent.gestures.map((gesture) => ({
        ...gesture,
        mapping: { kind: "switch_next", enabled: false },
      })),
    };
    const client = new StubClient(disabledLibrary);
    const { container } = await renderScreen(
      withSettings(
        client,
        <Gestures client={client} connectionState="open" />,
      ),
    );

    const select = container.querySelector<HTMLSelectElement>("#gesture-action-7");
    expect(select).not.toBeNull();
    await act(async () => {
      if (select !== null) {
        select.value = actionKey({ kind: "switch_previous" });
        select.dispatchEvent(new Event("change", { bubbles: true }));
      }
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(
      client.requested.filter(({ name }) => name === "set_mapping").at(-1),
    ).toEqual({
      name: "set_mapping",
      fields: {
        gesture_id: 7,
        action: { kind: "switch_previous" },
        enabled: false,
      },
    });

    const mappingSwitch = container.querySelector<HTMLButtonElement>(
      '[role="switch"][aria-label="Enable Desk wave"]',
    );
    expect(mappingSwitch?.getAttribute("aria-checked")).toBe("false");
    await act(async () => {
      mappingSwitch?.click();
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(
      client.requested.filter(({ name }) => name === "set_mapping").at(-1),
    ).toEqual({
      name: "set_mapping",
      fields: {
        gesture_id: 7,
        action: { kind: "switch_next" },
        enabled: true,
      },
    });
  });

  it("disables the mapping switch when a gesture has no mapped action", async () => {
    const unmappedLibrary: LibraryEvent = {
      ...libraryEvent,
      gestures: libraryEvent.gestures.map((gesture) => ({
        ...gesture,
        mapping: null,
      })),
    };
    const client = new StubClient(unmappedLibrary);
    const { container } = await renderScreen(
      withSettings(
        client,
        <Gestures client={client} connectionState="open" />,
      ),
    );

    expect(
      container.querySelector<HTMLButtonElement>(
        '[role="switch"][aria-label="Enable Desk wave"]',
      )?.disabled,
    ).toBe(true);
  });

  it("marks disabled mappings as disabled on the Dashboard", async () => {
    const disabledLibrary: LibraryEvent = {
      ...libraryEvent,
      gestures: libraryEvent.gestures.map((gesture) => ({
        ...gesture,
        mapping: { kind: "switch_next", enabled: false },
      })),
    };
    const client = new StubClient(disabledLibrary);
    const { container } = await renderScreen(
      withSettings(
        client,
        <Dashboard
          client={client}
          connectionState="open"
          onNavigate={() => undefined}
        />,
      ),
    );

    expect(container.querySelector(".mapping-summary-list strong")?.textContent).toBe(
      "Next app · Disabled",
    );
    expect(container.textContent).not.toContain("not exposed by the daemon");
  });

  it("renders Settings privacy and honest Calibration status", async () => {
    const client = new StubClient();
    const { container: settings } = await renderScreen(
      withSettings(client, <Settings client={client} connectionState="open" />),
    );

    expect(settings.textContent).toContain("Effective daemon configuration");
    expect(settings.textContent).toContain("Privacy and local data");
    expect(settings.textContent).toContain("C:\\AirControl\\aircontrol.db");

    const { container: calibration } = await renderScreen(
      withSettings(
        client,
        <Calibration client={client} connectionState="open" />,
      ),
    );

    expect(calibration.textContent).toContain("Calibrated");
    expect(calibration.textContent).toContain("0.1432");
    expect(calibration.textContent).toContain("Acceptable");
    expect(calibration.textContent).not.toContain("Unknown");
    expect(calibration.textContent).toContain("calibrate.cmd");
  });

  it("renders and updates the click mode setting", async () => {
    const client = new StubClient();
    const { container } = await renderScreen(
      withSettings(client, <Settings client={client} connectionState="open" />),
    );

    expect(
      container.querySelector<HTMLLabelElement>(
        'label[for="settings-click-mode"]',
      )?.textContent,
    ).toBe("Click mode");
    const clickMode = container.querySelector<HTMLSelectElement>(
      "#settings-click-mode",
    );
    expect(clickMode?.value).toBe("single");
    expect(
      Array.from(clickMode?.options ?? []).map((option) => option.textContent),
    ).toContain("Two hands (point + pinch)");

    await act(async () => {
      if (clickMode !== null) {
        clickMode.value = "two_hand";
        clickMode.dispatchEvent(new Event("change", { bubbles: true }));
      }
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(
      client.requested
        .filter(({ name }) => name === "set_app_setting")
        .at(-1),
    ).toEqual({
      name: "set_app_setting",
      fields: { key: "click_mode", value: "two_hand" },
    });
  });

  it("keeps Advanced tuning collapsed until the user expands it", async () => {
    const client = new StubClient();
    const { container } = await renderScreen(
      withSettings(client, <Settings client={client} connectionState="open" />),
    );

    const advanced = container.querySelector<HTMLDetailsElement>(
      "#settings-advanced-tuning",
    );
    expect(advanced).not.toBeNull();
    expect(advanced?.open).toBe(false);
    expect(advanced?.querySelector("summary")?.textContent).toContain(
      "Advanced tuning",
    );

    await act(async () => {
      advanced?.querySelector<HTMLElement>("summary")?.click();
      await Promise.resolve();
    });

    expect(advanced?.open).toBe(true);
    const groupNames = Array.from(advanced?.querySelectorAll("legend") ?? []).map(
      (legend) => legend.textContent,
    );
    expect(groupNames).toEqual(["Pointer", "Click", "Gestures"]);
    expect(
      advanced?.querySelector<HTMLLabelElement>(
        'label[for="settings-pointer-responsiveness"]',
      )?.textContent,
    ).toBe("Pointer responsiveness");
  });

  it("renders and updates the drag-lock setting", async () => {
    const client = new StubClient();
    const { container } = await renderScreen(
      withSettings(client, <Settings client={client} connectionState="open" />),
    );
    const advanced = container.querySelector<HTMLDetailsElement>(
      "#settings-advanced-tuning",
    );
    const dragLock = advanced?.querySelector<HTMLButtonElement>(
      '[role="switch"][aria-labelledby="settings-drag-lock-label"]',
    );

    expect(
      advanced?.querySelector<HTMLElement>("#settings-drag-lock-label")
        ?.textContent,
    ).toBe("Drag lock");
    expect(dragLock?.getAttribute("aria-checked")).toBe("true");

    await act(async () => {
      dragLock?.click();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(
      client.requested
        .filter(({ name }) => name === "set_app_setting")
        .at(-1),
    ).toEqual({
      name: "set_app_setting",
      fields: { key: "drag_lock_enabled", value: false },
    });
  });

  it("debounces an advanced slider change before saving the right key", async () => {
    vi.useFakeTimers();
    try {
      const client = new StubClient();
      const { container } = await renderScreen(
        withSettings(
          client,
          <Settings client={client} connectionState="open" />,
        ),
      );
      const slider = container.querySelector<HTMLInputElement>(
        "#settings-pointer-responsiveness",
      );
      expect(slider).not.toBeNull();

      await act(async () => {
        if (slider !== null) {
          const valueSetter = Object.getOwnPropertyDescriptor(
            HTMLInputElement.prototype,
            "value",
          )?.set;
          valueSetter?.call(slider, "73");
          slider.dispatchEvent(new Event("input", { bubbles: true }));
        }
        vi.advanceTimersByTime(149);
        await Promise.resolve();
      });
      expect(
        client.requested.filter(({ name }) => name === "set_app_setting"),
      ).toHaveLength(0);

      await act(async () => {
        vi.advanceTimersByTime(1);
        await Promise.resolve();
        await Promise.resolve();
      });

      expect(
        client.requested
          .filter(({ name }) => name === "set_app_setting")
          .at(-1),
      ).toEqual({
        name: "set_app_setting",
        fields: { key: "pointer_responsiveness", value: 73 },
      });
    } finally {
      vi.useRealTimers();
    }
  });

  it("resets advanced settings and refreshes their displayed values", async () => {
    const client = new StubClient();
    const { container } = await renderScreen(
      withSettings(client, <Settings client={client} connectionState="open" />),
    );
    const responsiveness = () =>
      container.querySelector<HTMLOutputElement>(
        'output[for="settings-pointer-responsiveness"]',
      )?.textContent;

    expect(responsiveness()).toBe("35");
    const resetButton = Array.from(
      container.querySelectorAll<HTMLButtonElement>("button"),
    ).find(
      (button) => button.textContent === "Reset advanced settings to defaults",
    );
    expect(resetButton).toBeDefined();

    await act(async () => {
      resetButton?.click();
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(
      client.requested
        .filter(({ name }) => name === "reset_app_settings")
        .at(-1),
    ).toEqual({ name: "reset_app_settings", fields: {} });
    expect(
      client.requested.filter(({ name }) => name === "get_app_settings"),
    ).toHaveLength(2);
    expect(responsiveness()).toBe("20");
  });

  it("reports when no calibration profile is active", async () => {
    const noProfileSettings: SettingsEvent = {
      ...effectiveSettingsEvent,
      payload: {
        ...effectiveSettingsEvent.payload,
        has_calibration_profile: false,
        calibration: undefined,
      },
    };
    const client = new StubClient(libraryEvent, noProfileSettings);
    const { container } = await renderScreen(
      withSettings(
        client,
        <Calibration client={client} connectionState="open" />,
      ),
    );

    expect(container.textContent).toContain("Not calibrated");
    expect(container.textContent).not.toContain("Unknown");
  });

  it("renders all Appearance choices and the Airy setting", async () => {
    const client = new StubClient();
    const { container } = await renderScreen(
      withSettings(client, <Appearance connectionState="open" />),
    );

    const options = container.querySelectorAll<HTMLInputElement>(
      'input[name="aircontrol-theme"]',
    );
    expect(options).toHaveLength(3);
    expect(container.textContent).toContain("Light");
    expect(container.textContent).toContain("Dark");
    expect(container.textContent).toContain("System");
    expect(container.textContent).toContain("Show Airy companion");
  });

  it("renders About version, guide, and never-video privacy statement", async () => {
    const client = new StubClient();
    const { container } = await renderScreen(
      withSettings(client, <About connectionState="open" />),
    );

    expect(container.textContent).toContain("Version 0.1.0");
    expect(container.textContent).toContain("User guide");
    expect(container.textContent).toContain("Built-in gesture vocabulary");
    expect(container.textContent?.toLowerCase()).toContain("never stores video");
  });
});
