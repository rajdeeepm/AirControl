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
    expect(stateName?.textContent).toBe("Inactive");
    expect(stateDetail?.textContent).toBe("Gesture tracking paused");
    expectArmPose("lowered");
    expect(socket.sent.at(-1)).toEqual({
      v: 1,
      type: "command",
      name: "get_status",
    });

    socket.emit("message", {
      data: JSON.stringify({ v: 1, type: "status", armed: true }),
    });
    expect(stateName?.textContent).toBe("Active");
    expect(stateDetail?.textContent).toBe("Tracking your gestures");
    expectArmPose("raised");

    document.querySelector<HTMLButtonElement>("#companion-button")?.click();
    expect(socket.sent.at(-1)).toEqual({
      v: 1,
      type: "command",
      name: "toggle_arm",
    });

    socket.emit("message", {
      data: JSON.stringify({ v: 1, type: "status", armed: false }),
    });
    expect(stateName?.textContent).toBe("Inactive");
    expectArmPose("lowered");

    document.querySelector<HTMLButtonElement>("#close-button")?.click();
    await Promise.resolve();
    expect(closeWidget).toHaveBeenCalledOnce();

    const card = document.querySelector<HTMLElement>("#airy-card");
    const dragSurface = document.querySelector<HTMLElement>(".status-pill");
    expect(card?.classList.contains("js-drag-fallback")).toBe(true);
    if (card === null || dragSurface === null) {
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
