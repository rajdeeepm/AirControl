import { act, type ReactElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AppSettingsProvider } from "../lib/app-settings";
import type {
  CommandFields,
  CommandName,
  ServerEvent,
  ServerEventType,
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
    dominant_hand: "right",
    theme: "system",
    airy_enabled: true,
  },
};

const libraryEvent: ServerEvent = {
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
      mapping: { kind: "switch_next" },
      animation: null,
    },
  ],
};

const effectiveSettingsEvent: ServerEvent = {
  v: 1,
  type: "settings",
  payload: {
    clutch_mode: "hold",
    gate_thresholds: { t1: 0.8, t2: 0.15, t3: 0.5 },
    camera_index: 0,
    store_db_path: "C:\\AirControl\\aircontrol.db",
  },
};

class StubClient {
  readonly sent: Array<{ name: CommandName; fields: CommandFields }> = [];
  private previewCallbacks = new Set<(frame: Blob) => void>();

  on(
    type: ServerEventType,
    callback: (event: ServerEvent) => void,
  ): () => void {
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
    return () => undefined;
  }

  send(name: CommandName, fields: CommandFields = {}): void {
    this.sent.push({ name, fields });
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
    void fields;
    if (name === "get_app_settings") {
      return Promise.resolve(appSettingsEvent);
    }
    if (name === "list_library") {
      return Promise.resolve(libraryEvent);
    }
    if (name === "get_settings") {
      return Promise.resolve(effectiveSettingsEvent);
    }
    return Promise.resolve({ v: 1, type: "ack", ok: true, error: "" });
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

    const armedSwitch = container.querySelector<HTMLElement>('[role="switch"]');
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

    expect(calibration.textContent).toContain("Unknown");
    expect(calibration.textContent).toContain("calibrate.cmd");
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
