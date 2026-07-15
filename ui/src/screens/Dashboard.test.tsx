import { act, type ReactElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { AppSettingsProvider } from "../lib/app-settings";
import type {
  CommandFields,
  CommandName,
  ServerEvent,
  ServerEventType,
  StatusEvent,
} from "../lib/types";
import { Dashboard } from "./Dashboard";

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });

class DashboardClientStub {
  readonly sent: Array<{ name: CommandName; fields: CommandFields }> = [];
  private readonly listeners = new Map<
    ServerEventType,
    Set<(event: ServerEvent) => void>
  >();

  on(
    type: ServerEventType,
    callback: (event: ServerEvent) => void,
  ): () => void {
    const callbacks = this.listeners.get(type) ?? new Set();
    callbacks.add(callback);
    this.listeners.set(type, callbacks);
    return () => callbacks.delete(callback);
  }

  onPreviewFrame(): () => void {
    return () => undefined;
  }

  send(name: CommandName, fields: CommandFields = {}): void {
    this.sent.push({ name, fields });
  }

  request(name: CommandName): Promise<ServerEvent> {
    if (name === "get_app_settings") {
      return Promise.resolve({
        v: 1,
        type: "app_settings",
        settings: {
          sensitivity: 50,
          smoothing: 64,
          cursor_speed: 1,
          dominant_hand: "right",
          theme: "system",
          airy_enabled: true,
        },
      });
    }
    if (name === "list_library") {
      return Promise.resolve({ v: 1, type: "library", gestures: [] });
    }
    return Promise.resolve({ v: 1, type: "ack", ok: true, error: "" });
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
});

describe("Dashboard armed control", () => {
  it("toggles by pointer, Enter, and Space while reflecting live status", async () => {
    const client = new DashboardClientStub();
    const container = await renderDashboard(client);

    act(() => client.emitStatus(false));

    const armedSwitch = container.querySelector<HTMLButtonElement>(
      '[role="switch"]',
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
});
