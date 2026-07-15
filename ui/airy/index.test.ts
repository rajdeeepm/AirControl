import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { afterEach, describe, expect, it, vi } from "vitest";

type Listener = (event: { data?: unknown }) => void;

class FakeWebSocket {
  static readonly OPEN = 1;
  static readonly instances: FakeWebSocket[] = [];

  readonly sent: Array<Record<string, unknown>> = [];
  readonly listeners = new Map<string, Listener[]>();
  readyState = 0;

  constructor(readonly url: string) {
    FakeWebSocket.instances.push(this);
  }

  addEventListener(type: string, listener: Listener): void {
    const listeners = this.listeners.get(type) ?? [];
    listeners.push(listener);
    this.listeners.set(type, listeners);
  }

  emit(type: string, event: { data?: unknown } = {}): void {
    this.listeners.get(type)?.forEach((listener) => listener(event));
  }

  send(payload: string): void {
    this.sent.push(JSON.parse(payload) as Record<string, unknown>);
  }

  close(): void {
    this.readyState = 3;
    this.emit("close");
  }
}

function pointerEvent(
  type: string,
  screenX: number,
  screenY: number,
): Event {
  const event = new Event(type, { bubbles: true, cancelable: true });
  Object.defineProperties(event, {
    button: { value: 0 },
    pointerId: { value: 7 },
    screenX: { value: screenX },
    screenY: { value: screenY },
  });
  return event;
}

function expectArmPose(pose: "raised" | "lowered"): void {
  const arm = document.querySelector(".arm");
  const otherPose = pose === "raised" ? "lowered" : "raised";

  expect(arm, "Airy's SVG arm should be present").not.toBeNull();
  expect(arm?.classList.contains(`arm-${pose}`)).toBe(true);
  expect(arm?.classList.contains(`arm-${otherPose}`)).toBe(false);
}

afterEach(() => {
  window.dispatchEvent(new Event("beforeunload"));
  vi.useRealTimers();
  document.head.replaceChildren();
  document.body.replaceChildren();
  FakeWebSocket.instances.length = 0;
  vi.restoreAllMocks();
  Reflect.deleteProperty(window, "pywebview");
  window.history.replaceState({}, "", "/");
});

describe("Airy static companion", () => {
  it("declares the offline feedback, reaction, settings, and control contracts", () => {
    const source = readFileSync(resolve("airy/index.html"), "utf8");
    const parsed = new DOMParser().parseFromString(source, "text/html");
    const script = parsed.querySelector("script")?.textContent ?? "";
    const visibleText = parsed.body.textContent?.replace(/\s+/g, " ") ?? "";

    expect(source).not.toMatch(/https?:\/\//i);
    for (const label of ["Active", "Inactive", "Offline", "Paused"]) {
      expect(source).toContain(label);
    }

    for (const field of [
      "hand_visible",
      "active_pose",
      "hold_progress",
      "gate",
      "reason",
      "confidence",
      "description",
      "camera_error",
    ]) {
      expect(script).toContain(field);
    }

    for (const category of [
      "click",
      "drag",
      "scroll",
      "window",
      "volume",
      "mute",
      "media",
      "system",
      "hotkey",
      "pointer",
    ]) {
      expect(source).toContain(`[data-category="${category}"]`);
    }

    for (const item of [
      "Pause",
      "Resume",
      "Open dashboard",
      "Camera status",
      "Exit",
      "Recalibrate",
      "Switch profile",
      "Open the dashboard to recalibrate",
    ]) {
      expect(visibleText).toContain(item);
    }

    for (const command of [
      "toggle_arm",
      "focus_dashboard",
      "quit",
      "get_settings",
      "get_app_settings",
    ]) {
      expect(script).toContain(command);
    }
    expect(script).not.toMatch(
      /sendCommand\(["'](?:recalibrate|switch_profile)["']\)/,
    );

    for (const setting of [
      "airy_feedback_level",
      "airy_sounds",
      "airy_animation_intensity",
      "airy_size",
    ]) {
      expect(script).toContain(setting);
    }
    for (const level of ["full", "subtle", "minimal", "hidden"]) {
      expect(source).toMatch(new RegExp(`["']${level}["']`));
    }

    expect(source).toMatch(/prefers-reduced-motion\s*:\s*reduce/);
    expect(source).toContain('aria-expanded="false"');
    expect(source).toContain('aria-controls="control-menu"');
    expect(source).toContain('id="action-label"');
    expect(script).toContain("DRAG_THRESHOLD_PX");
    expect(script).toContain("ACTION_LABEL_MS");
    expect(script).toContain("AudioContext");
    expect(script).toContain("left_up");
    expect(script).toMatch(/secondary|right_click/i);
    expect(script).toContain("Escape");
  });

  it("renders honest states and wires toggle, close, and drag fallback", async () => {
    const source = readFileSync(resolve("airy/index.html"), "utf8");
    expect(source).not.toMatch(/https?:\/\//i);
    expect(source).toContain("pywebviewready");
    expect(source).toContain("get_bootstrap");
    expect(source).toContain("ws://127.0.0.1:8787");
    expect(source).not.toContain("URLSearchParams");
    expect(source).not.toContain("location.search");

    const parsed = new DOMParser().parseFromString(source, "text/html");
    document.body.innerHTML = parsed.body.innerHTML;

    const closeWidget = vi.fn(async () => true);
    const moveWindow = vi.fn();
    const getBootstrap = vi.fn(async () => ({
      ws_url: "ws://127.0.0.1:9876",
      native_drag: false,
    }));
    const bridge: {
      api?: {
        close_widget: typeof closeWidget;
        get_bootstrap: typeof getBootstrap;
        move_window: typeof moveWindow;
      };
    } = {};
    Object.defineProperties(window, {
      WebSocket: { configurable: true, value: FakeWebSocket },
      pywebview: { configurable: true, value: bridge },
      requestAnimationFrame: {
        configurable: true,
        value: (callback: FrameRequestCallback) => {
          callback(0);
          return 1;
        },
      },
      screenX: { configurable: true, value: 50 },
      screenY: { configurable: true, value: 60 },
    });

    const script = parsed.querySelector("script")?.textContent;
    expect(script).toBeTruthy();
    window.eval(script ?? "");

    const stateName = document.querySelector("#status-name");
    const stateDetail = document.querySelector("#status-detail");
    const card = document.querySelector<HTMLElement>("#airy-card");
    if (card === null) {
      throw new Error("Airy's state surface was not rendered");
    }
    expect(stateName?.textContent).toBe("Offline");
    expect(stateDetail?.textContent).toBe("AirControl is not running");
    expectArmPose("lowered");
    expect(FakeWebSocket.instances).toHaveLength(0);

    bridge.api = {
      close_widget: closeWidget,
      get_bootstrap: getBootstrap,
      move_window: moveWindow,
    };
    window.dispatchEvent(new Event("pywebviewready"));
    await vi.waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
    expect(getBootstrap).toHaveBeenCalledOnce();

    const socket = FakeWebSocket.instances[0];
    expect(socket.url).toBe("ws://127.0.0.1:9876/");
    socket.readyState = FakeWebSocket.OPEN;
    socket.emit("open");
    expect(stateName?.textContent).toBe("Offline");
    expect(stateDetail?.textContent).toBe("Waiting for AirControl status");
    expectArmPose("lowered");
    expect(socket.sent).toEqual(
      expect.arrayContaining([
        { v: 1, type: "command", name: "get_status" },
        { v: 1, type: "command", name: "get_settings" },
        { v: 1, type: "command", name: "get_app_settings" },
      ]),
    );

    socket.emit("message", {
      data: JSON.stringify({
        v: 1,
        type: "app_settings",
        settings: {
          airy_feedback_level: "full",
          airy_sounds: false,
          airy_animation_intensity: 80,
          airy_size: "medium",
        },
      }),
    });
    socket.emit("message", {
      data: JSON.stringify({
        v: 1,
        type: "camera",
        state: "active",
        camera_error: null,
      }),
    });

    socket.emit("message", {
      data: JSON.stringify({
        v: 1,
        type: "status",
        armed: true,
        raw_pose: "Open palm",
        active_pose: "Open palm",
        hold_progress: 0,
        hand_visible: true,
        status_text: "Tracking your gestures",
      }),
    });
    expect(stateName?.textContent).toBe("Active");
    expect(stateDetail?.textContent).toBe("Tracking your gestures");
    expectArmPose("raised");

    socket.emit("message", {
      data: JSON.stringify({
        v: 1,
        type: "status",
        armed: true,
        raw_pose: "Pointer",
        active_pose: "Pointer",
        hold_progress: 0.5,
        hand_visible: true,
        status_text: "Pointer tracking",
      }),
    });
    expect(card.dataset.pose).toBe("pointer");
    expect(card.dataset.hold).toBe("true");
    expect(card.style.getPropertyValue("--hold-progress")).toBe("0.5");

    socket.emit("message", {
      data: JSON.stringify({
        v: 1,
        type: "candidate",
        gate: "abstain",
        reason: "below_t1",
        confidence: 0.41,
        ts: 1,
      }),
    });
    expect(card.dataset.candidate).toBe("abstain");
    socket.emit("message", {
      data: JSON.stringify({
        v: 1,
        type: "candidate",
        gate: "fire",
        reason: "passed",
        confidence: 0.91,
        ts: 2,
      }),
    });
    expect(card.dataset.candidate).toBe("fire");

    const action = (
      kind: string,
      category: string,
      description: string,
      extra: Record<string, unknown> = {},
    ) => socket.emit("message", {
      data: JSON.stringify({
        v: 1,
        type: "action",
        kind,
        category,
        confidence: 0.93,
        description,
        ts: 3,
        ...extra,
      }),
    });

    action("left_down", "click", "PINCH · BUTTON DOWN");
    action("move_pointer", "pointer", "");
    expect(card.dataset.category).toBe("drag");
    expect(card.dataset.reactionPhase).toBe("active");
    action("left_up", "click", "PINCH RELEASED");
    expect(card.dataset.category).toBe("drag");
    expect(card.dataset.reactionPhase).toBe("settle");

    action("left_down", "click", "PINCH · BUTTON DOWN");
    action("left_up", "click", "PINCH RELEASED");
    expect(card.dataset.category).toBe("click");
    expect(document.querySelector("#action-label")?.textContent).toBe(
      "LEFT CLICK",
    );

    action("scroll", "scroll", "SCROLL DOWN");
    expect(card.dataset.category).toBe("scroll");
    expect(card.dataset.direction).toBe("down");
    action("switch_previous", "window", "PREVIOUS APP");
    expect(card.dataset.category).toBe("window");
    expect(card.dataset.direction).toBe("left");
    action("hotkey", "volume", "HOTKEY · VK_0xAF");
    expect(card.dataset.category).toBe("volume");
    action("hotkey", "mute", "HOTKEY · VK_0xAD");
    expect(card.dataset.category).toBe("mute");
    action("hotkey", "media", "HOTKEY · VK_0xB3");
    expect(card.dataset.category).toBe("media");
    action("escape", "system", "UNDO · ESC");
    expect(card.dataset.category).toBe("system");
    action("move_pointer", "pointer", "");
    expect(card.dataset.category).toBe("pointer");
    expect(card.style.getPropertyValue("--pupil-x")).toBe("0.00px");
    action("hotkey", "hotkey", "RIGHT CLICK");
    expect(card.dataset.secondary).toBe("true");
    expect(document.querySelector("#action-label")?.textContent).toBe(
      "RIGHT CLICK",
    );

    socket.emit("message", {
      data: JSON.stringify({
        v: 1,
        type: "camera",
        state: "error",
        camera_error: "Camera unplugged",
      }),
    });
    expect(card.dataset.cameraState).toBe("error");
    expect(stateName?.textContent).toBe("Camera error");
    expect(stateDetail?.textContent).toBe("Camera unplugged");
    socket.emit("message", {
      data: JSON.stringify({
        v: 1,
        type: "camera",
        state: "active",
        camera_error: null,
      }),
    });

    socket.emit("message", {
      data: JSON.stringify({
        v: 1,
        type: "app_settings",
        settings: {
          airy_feedback_level: "minimal",
          airy_animation_intensity: 35,
          airy_size: "large",
        },
      }),
    });
    expect(card.dataset.feedbackLevel).toBe("minimal");
    expect(card.dataset.reducedMotion).toBe("true");
    expect(card.dataset.size).toBe("large");
    socket.emit("message", {
      data: JSON.stringify({
        v: 1,
        type: "app_settings",
        settings: { airy_feedback_level: "hidden" },
      }),
    });
    expect(card.hidden).toBe(true);
    socket.emit("message", {
      data: JSON.stringify({
        v: 1,
        type: "app_settings",
        settings: {
          airy_feedback_level: "full",
          airy_animation_intensity: 80,
          airy_size: "medium",
        },
      }),
    });
    expect(card.hidden).toBe(false);

    socket.emit("message", {
      data: JSON.stringify({
        v: 1,
        type: "status",
        armed: true,
        raw_pose: "No hand",
        active_pose: "Neutral",
        hold_progress: 0,
        hand_visible: false,
        status_text: "Looking for your hand",
      }),
    });
    expect(card.dataset.detection).toBe("scan");

    const commandsBeforeMenu = socket.sent.length;
    document.querySelector<HTMLButtonElement>("#companion-button")?.click();
    expect(socket.sent).toHaveLength(commandsBeforeMenu);
    expect(
      document.querySelector<HTMLButtonElement>("#companion-button")
        ?.getAttribute("aria-expanded"),
    ).toBe("true");
    expect(
      document.querySelector<HTMLElement>("#control-menu")?.hidden,
    ).toBe(false);

    document.querySelector<HTMLButtonElement>("#pause-button")?.click();
    expect(socket.sent.at(-1)).toEqual({
      v: 1,
      type: "command",
      name: "toggle_arm",
    });

    socket.emit("message", {
      data: JSON.stringify({
        v: 1,
        type: "status",
        armed: false,
        raw_pose: "Neutral",
        active_pose: "Neutral",
        hold_progress: 0,
        hand_visible: true,
        status_text: "Paused manually",
      }),
    });
    expect(stateName?.textContent).toBe("Inactive");
    expect(stateDetail?.textContent).toBe("Paused manually");
    expectArmPose("lowered");

    document.querySelector<HTMLButtonElement>("#companion-button")?.click();
    expect(document.querySelector("#pause-button")?.textContent).toContain(
      "Resume",
    );
    document.querySelector<HTMLButtonElement>("#dashboard-button")?.click();
    expect(socket.sent.at(-1)).toEqual({
      v: 1,
      type: "command",
      name: "focus_dashboard",
    });

    document.querySelector<HTMLButtonElement>("#close-button")?.click();
    await Promise.resolve();
    expect(closeWidget).toHaveBeenCalledOnce();

    const dragSurface = document.querySelector<HTMLElement>(".status-pill");
    expect(card.classList.contains("js-drag-fallback")).toBe(true);
    if (dragSurface === null) {
      throw new Error("Airy drag surface was not rendered");
    }
    Object.assign(card, {
      setPointerCapture: () => undefined,
      hasPointerCapture: () => false,
      releasePointerCapture: () => undefined,
    });
    dragSurface.dispatchEvent(pointerEvent("pointerdown", 200, 300));
    card.dispatchEvent(pointerEvent("pointermove", 215, 310));
    expect(moveWindow).toHaveBeenLastCalledWith(65, 70);

    const companion = document.querySelector<HTMLButtonElement>(
      "#companion-button",
    );
    if (companion === null) {
      throw new Error("Airy's clickable drag surface was not rendered");
    }
    companion.dispatchEvent(pointerEvent("pointerdown", 100, 100));
    companion.dispatchEvent(pointerEvent("pointermove", 112, 109));
    companion.dispatchEvent(pointerEvent("pointerup", 112, 109));
    companion.click();
    expect(
      document.querySelector<HTMLElement>("#control-menu")?.hidden,
    ).toBe(true);
  });

  it("uses safe standalone defaults when the pywebview bridge is unavailable", async () => {
    vi.useFakeTimers();
    const source = readFileSync(resolve("airy/index.html"), "utf8");
    const parsed = new DOMParser().parseFromString(source, "text/html");
    document.body.innerHTML = parsed.body.innerHTML;
    Object.defineProperties(window, {
      WebSocket: { configurable: true, value: FakeWebSocket },
      pywebview: { configurable: true, value: {} },
    });

    const script = parsed.querySelector("script")?.textContent;
    expect(script).toBeTruthy();
    window.eval(script ?? "");

    expect(document.querySelector("#status-name")?.textContent).toBe("Offline");
    expect(FakeWebSocket.instances).toHaveLength(0);

    await vi.advanceTimersByTimeAsync(1999);
    expect(FakeWebSocket.instances).toHaveLength(0);

    await vi.advanceTimersByTimeAsync(1);

    expect(FakeWebSocket.instances).toHaveLength(1);
    expect(FakeWebSocket.instances[0].url).toBe("ws://127.0.0.1:8787");
    expect(document.querySelector("#airy-card")?.classList).toContain(
      "js-drag-fallback",
    );
  });
});
