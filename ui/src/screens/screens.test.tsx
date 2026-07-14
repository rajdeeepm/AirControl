import { act, ReactElement } from "react";
import { createRoot, Root } from "react-dom/client";
import { afterEach, describe, expect, it } from "vitest";
import {
  CommandFields,
  CommandName,
  ServerEvent,
  ServerEventType,
} from "../lib/types";
import { ConnectionState } from "../lib/ws";
import { Library } from "./Library";
import { Mappings } from "./Mappings";
import { Settings } from "./Settings";
import { Status } from "./Status";

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });

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

class StubClient {
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
        status_text: "Armed and watching",
      });
    }
    return () => undefined;
  }

  onState(callback: (state: ConnectionState) => void): () => void {
    callback("open");
    return () => undefined;
  }

  send(name: CommandName, fields: CommandFields = {}): void {
    void name;
    void fields;
  }

  request(
    name: CommandName,
    fields: CommandFields = {},
  ): Promise<ServerEvent> {
    void fields;
    if (name === "list_library") return Promise.resolve(libraryEvent);
    if (name === "get_metrics") {
      return Promise.resolve({
        v: 1,
        type: "metrics_snapshot",
        candidates_per_hour: 12.5,
        armed_candidates_per_hour: 8.25,
        fp_per_hour: 0.1,
        latency_ms_p50: 18.75,
        latency_ms_p95: 31.5,
        cpu_pct: 4.2,
        uptime_seconds: 120,
      });
    }
    if (name === "get_settings") {
      return Promise.resolve({
        v: 1,
        type: "settings",
        payload: {
          clutch_mode: "hold",
          gate_thresholds: { t1: 0.8, t2: 0.15, t3: 0.5 },
          camera_index: 0,
          store_db_path: "C:\\AirControl\\aircontrol.db",
        },
      });
    }
    return Promise.resolve({ v: 1, type: "ack", ok: true, error: "" });
  }
}

const roots: Root[] = [];

async function renderScreen(element: ReactElement): Promise<HTMLElement> {
  const container = document.createElement("div");
  document.body.append(container);
  const root = createRoot(container);
  roots.push(root);
  await act(async () => {
    root.render(element);
    await Promise.resolve();
    await Promise.resolve();
  });
  return container;
}

afterEach(() => {
  for (const root of roots.splice(0)) {
    act(() => root.unmount());
  }
  document.body.replaceChildren();
});

describe("application screens", () => {
  it("renders the armed status banner", async () => {
    const container = await renderScreen(
      <Status client={new StubClient()} connectionState="open" />,
    );

    const banner = container.querySelector(".armed-banner");
    expect(banner?.getAttribute("data-armed")).toBe("true");
    expect(banner?.textContent).toContain("Armed and watching");
  });

  it("renders a custom gesture and the built-in vocabulary", async () => {
    const container = await renderScreen(
      <Library client={new StubClient()} connectionState="open" />,
    );

    expect(container.textContent).toContain("Desk wave");
    expect(container.textContent).toContain("Built-in");
    expect(container.textContent).toContain("Open palm hold");
  });

  it("renders the curated mapping catalog", async () => {
    const container = await renderScreen(
      <Mappings client={new StubClient()} connectionState="open" />,
    );

    expect(container.textContent).toContain("Media");
    expect(container.textContent).toContain("Play/Pause");
  });

  it("renders the on-device privacy statement", async () => {
    const container = await renderScreen(
      <Settings client={new StubClient()} connectionState="open" />,
    );

    expect(container.textContent?.toLowerCase()).toContain(
      "all processing is on-device",
    );
    expect(container.textContent).toContain("C:\\AirControl\\aircontrol.db");
  });
});
