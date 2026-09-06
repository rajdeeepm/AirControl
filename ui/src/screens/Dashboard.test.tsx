import { act, type ReactElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AppSettingsProvider } from "../lib/app-settings";
import type {
  CameraEvent,
  CameraState,
  CandidateEvent,
  CommandFields,
  CommandName,
  ServerEvent,
  ServerEventType,
  SettingsEvent,
  StatusEvent,
} from "../lib/types";
import { Dashboard } from "./Dashboard";

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });

class DashboardClientStub {
  readonly sent: Array<{ name: CommandName; fields: CommandFields }> = [];
  readonly requested: Array<{ name: CommandName; fields: CommandFields }> = [];
  private readonly listeners = new Map<
    ServerEventType,
    Set<(event: ServerEvent) => void>
  >();
  private readonly previewCallbacks = new Set<(frame: Blob) => void>();

  constructor(
    private readonly initialCameraState: CameraState = "active",
    private readonly initialCameraError: string | null = null,
  ) {}

  on(
    type: ServerEventType,
    callback: (event: ServerEvent) => void,
  ): () => void {
    const callbacks = this.listeners.get(type) ?? new Set();
    callbacks.add(callback);
    this.listeners.set(type, callbacks);
    return () => callbacks.delete(callback);
  }

  onPreviewFrame(callback: (frame: Blob) => void): () => void {
    this.previewCallbacks.add(callback);
    return () => this.previewCallbacks.delete(callback);
  }

  send(name: CommandName, fields: CommandFields = {}): void {
    this.sent.push({ name, fields });
  }

  request(
    name: CommandName,
    fields: CommandFields = {},
  ): Promise<ServerEvent> {
    this.requested.push({ name, fields });
    if (name === "get_app_settings") {
      return Promise.resolve({
        v: 1,
        type: "app_settings",
        settings: {
          sensitivity: 50,
          smoothing: 64,
          cursor_speed: 1,
          dominant_hand: "right",
          click_mode: "single",
          theme: "system",
          airy_enabled: true,
        },
      });
    }
    if (name === "list_library") {
      return Promise.resolve({ v: 1, type: "library", gestures: [] });
    }
    if (name === "get_settings") {
      const event: SettingsEvent = {
        v: 1,
        type: "settings",
        payload: {
          has_calibration_profile: false,
          camera_state: this.initialCameraState,
          camera_error: this.initialCameraError,
        },
      };
      return Promise.resolve(event);
    }
    return Promise.resolve({ v: 1, type: "ack", ok: true, error: "" });
  }

  emitCamera(state: CameraState, cameraError: string | null = null): void {
    const event: CameraEvent = {
      v: 1,
      type: "camera",
      state,
      camera_error: cameraError,
    };
    this.listeners.get("camera")?.forEach((callback) => callback(event));
  }

  emitPreviewFrame(frame: Blob): void {
    this.previewCallbacks.forEach((callback) => callback(frame));
  }

  emitCandidate(confidence: number): void {
    const event: CandidateEvent = {
      v: 1,
      type: "candidate",
      gate: "fire",
      reason: "test candidate",
      confidence,
      ts: 1,
    };
    this.listeners.get("candidate")?.forEach((callback) => callback(event));
  }

  emitStatus(armed: boolean): void {
    const event: StatusEvent = {
      v: 1,
      type: "status",
      armed,
      raw_pose: armed ? "OPEN_PALM" : "NONE",
      active_pose: armed ? "OPEN_PALM" : "NONE",
      hold_progress: 0,
      hand_visible: true,
      status_text: armed ? "Armed and tracking" : "Ready to arm",
    };
    this.listeners.get("status")?.forEach((callback) => callback(event));
  }
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

let root: Root | null = null;

async function renderDashboard(
  client: DashboardClientStub,
): Promise<HTMLElement> {
  const container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  const element: ReactElement = (
    <AppSettingsProvider client={client} connectionState="open">
      <Dashboard
        client={client}
        connectionState="open"
        onNavigate={() => undefined}
      />
    </AppSettingsProvider>
  );
  await act(async () => {
    root?.render(element);
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });
  return container;
}

function pressKey(element: HTMLElement, key: string): void {
  element.dispatchEvent(
    new KeyboardEvent("keydown", { key, bubbles: true, cancelable: true }),
  );
  element.dispatchEvent(
    new KeyboardEvent("keyup", { key, bubbles: true, cancelable: true }),
  );
}

beforeEach(() => {
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: matchMedia,
  });
});

afterEach(() => {
  if (root !== null) {
    act(() => root?.unmount());
    root = null;
  }
  document.body.replaceChildren();
  document.documentElement.removeAttribute("data-theme");
  document.documentElement.style.removeProperty("color-scheme");
  vi.restoreAllMocks();
});

describe("Dashboard camera control", () => {
  it.each([
    ["off", "Camera is off", "Turn on camera"],
    ["starting", "Starting camera…", null],
    ["error", "Windows camera privacy settings", "Retry"],
    ["active", "Waiting for live camera preview…", null],
  ] as const)(
    "renders the %s state from get_settings",
    async (cameraState, expectedText, expectedButton) => {
      const cameraError =
        cameraState === "error"
          ? "Camera unavailable, check Windows camera privacy settings."
          : null;
      const client = new DashboardClientStub(cameraState, cameraError);
      const container = await renderDashboard(client);

      expect(client.requested.some(({ name }) => name === "get_settings")).toBe(
        true,
      );
      expect(container.textContent).toContain(expectedText);
      if (expectedButton !== null) {
        expect(
          Array.from(container.querySelectorAll("button")).some(
            (button) => button.textContent === expectedButton,
          ),
        ).toBe(true);
      }
      expect(
        client.sent.some(
          ({ name, fields }) =>
            name === "set_preview" && fields.enabled === true,
        ),
      ).toBe(cameraState === "active");
    },
  );

  it("sends camera toggle commands by pointer, Enter, and Space", async () => {
    const client = new DashboardClientStub("off");
    const container = await renderDashboard(client);
    const cameraSwitch = container.querySelector<HTMLButtonElement>(
      '[role="switch"][aria-labelledby="dashboard-camera-label"]',
    );

    expect(cameraSwitch).not.toBeNull();
    expect(cameraSwitch?.getAttribute("aria-checked")).toBe("false");

    act(() => cameraSwitch?.click());
    expect(client.sent.at(-1)).toEqual({
      name: "set_camera",
      fields: { enabled: true },
    });

    act(() => client.emitCamera("active"));
    expect(cameraSwitch?.getAttribute("aria-checked")).toBe("true");
    act(() => {
      if (cameraSwitch !== null) pressKey(cameraSwitch, "Enter");
    });
    expect(client.sent.at(-1)).toEqual({
      name: "set_camera",
      fields: { enabled: false },
    });

    act(() => client.emitCamera("off"));
    act(() => {
      if (cameraSwitch !== null) pressKey(cameraSwitch, " ");
    });
    expect(client.sent.at(-1)).toEqual({
      name: "set_camera",
      fields: { enabled: true },
    });
  });

  it("offers direct recovery actions for off and error states", async () => {
    const offClient = new DashboardClientStub("off");
    const offContainer = await renderDashboard(offClient);
    const turnOn = Array.from(offContainer.querySelectorAll("button")).find(
      (button) => button.textContent === "Turn on camera",
    );
    act(() => turnOn?.click());
    expect(offClient.sent.at(-1)).toEqual({
      name: "set_camera",
      fields: { enabled: true },
    });

    act(() => offClient.emitCamera("error", "Camera is blocked by Windows."));
    const retry = Array.from(offContainer.querySelectorAll("button")).find(
      (button) => button.textContent === "Retry",
    );
    expect(offContainer.textContent).toContain("Camera is blocked by Windows.");
    act(() => retry?.click());
    expect(offClient.sent.at(-1)).toEqual({
      name: "retry_camera",
      fields: {},
    });
  });

  it("stops preview streaming and revokes the frame when camera activity is lost", async () => {
    Object.defineProperties(URL, {
      createObjectURL: {
        configurable: true,
        value: vi.fn(() => "blob:camera-frame"),
      },
      revokeObjectURL: { configurable: true, value: vi.fn() },
    });
    const client = new DashboardClientStub("active");
    const container = await renderDashboard(client);

    await act(async () => {
      client.emitPreviewFrame(new Blob(["frame"], { type: "image/jpeg" }));
    });
    expect(container.querySelector(".camera-hero-frame img")).not.toBeNull();

    act(() => client.emitCamera("error", "Camera stopped responding."));
    expect(container.querySelector(".camera-hero-frame img")).toBeNull();
    expect(container.textContent).toContain("Camera stopped responding.");
    expect(client.sent.at(-1)).toEqual({
      name: "set_preview",
      fields: { enabled: false },
    });
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:camera-frame");
  });

  it("renders native armed/paused and detected-pose status chrome on the preview frame", async () => {
    const client = new DashboardClientStub("active");
    const container = await renderDashboard(client);

    act(() => client.emitStatus(false));
    let badge = container.querySelector(".camera-hero-armed-badge");
    expect(badge?.textContent).toBe("Paused");
    expect(container.textContent).toContain("Detected: No hand");

    act(() => client.emitStatus(true));
    badge = container.querySelector(".camera-hero-armed-badge");
    expect(badge?.textContent).toBe("Armed");
    expect(container.textContent).toContain("Detected: Open Palm");
  });

  it("clears stale confidence when the camera leaves active state", async () => {
    const client = new DashboardClientStub("active");
    const container = await renderDashboard(client);

    act(() => client.emitCandidate(0.87));
    expect(container.textContent).toContain("87.00%");

    act(() => client.emitCamera("error", "Camera stopped responding."));
    expect(container.textContent).not.toContain("87.00%");
    expect(container.textContent).toContain("No confidence data yet");
  });
});

describe("Dashboard armed control", () => {
  it("toggles by pointer, Enter, and Space while reflecting live status", async () => {
    const client = new DashboardClientStub();
    const container = await renderDashboard(client);

    act(() => client.emitStatus(false));

    const armedSwitch = container.querySelector<HTMLButtonElement>(
      '[role="switch"][aria-labelledby="dashboard-armed-label"]',
    );
    const visibleLabel = container.querySelector<HTMLElement>(
      "#dashboard-armed-label",
    );
    expect(armedSwitch).not.toBeNull();
    expect(visibleLabel?.textContent).toBe("Gesture control");
    expect(armedSwitch?.getAttribute("aria-labelledby")).toBe(
      "dashboard-armed-label",
    );
    expect(armedSwitch?.getAttribute("aria-checked")).toBe("false");

    act(() => armedSwitch?.click());
    expect(client.sent.filter(({ name }) => name === "toggle_arm")).toHaveLength(1);
    expect(armedSwitch?.getAttribute("aria-checked")).toBe("false");

    act(() => client.emitStatus(true));
    expect(armedSwitch?.getAttribute("aria-checked")).toBe("true");
    expect(container.textContent).toContain("ARMED");

    act(() => {
      if (armedSwitch !== null) pressKey(armedSwitch, "Enter");
    });
    expect(client.sent.filter(({ name }) => name === "toggle_arm")).toHaveLength(2);

    act(() => client.emitStatus(false));
    expect(armedSwitch?.getAttribute("aria-checked")).toBe("false");
    expect(container.textContent).toContain("IDLE");

    act(() => {
      if (armedSwitch !== null) pressKey(armedSwitch, " ");
    });
    expect(client.sent.filter(({ name }) => name === "toggle_arm")).toHaveLength(3);
  });

  it("makes arming unavailable until the camera is active", async () => {
    const client = new DashboardClientStub("off");
    const container = await renderDashboard(client);
    act(() => client.emitStatus(true));

    const armedSwitch = container.querySelector<HTMLButtonElement>(
      '[role="switch"][aria-labelledby="dashboard-armed-label"]',
    );
    expect(armedSwitch?.disabled).toBe(true);
    expect(armedSwitch?.getAttribute("aria-checked")).toBe("false");
    expect(container.textContent).not.toContain("ARMED");
    expect(container.textContent).toContain("Camera must be active to arm");

    act(() => client.emitCamera("active"));
    expect(armedSwitch?.disabled).toBe(false);
  });
});
