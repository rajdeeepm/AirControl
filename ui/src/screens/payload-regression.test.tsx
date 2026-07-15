import { act, type ReactElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { AppSettingsProvider } from "../lib/app-settings";
import {
  FRESH_APP_SETTINGS_EVENT,
  FRESH_EFFECTIVE_SETTINGS_EVENT,
  FRESH_LIBRARY_EVENT,
} from "../lib/daemon-fixtures";
import type {
  CommandFields,
  CommandName,
  ServerEvent,
  ServerEventType,
} from "../lib/types";
import { Appearance } from "./Appearance";
import { Calibration } from "./Calibration";
import { Settings } from "./Settings";

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });

class FreshDaemonClient {
  on(
    _type: ServerEventType,
    _callback: (event: ServerEvent) => void,
  ): () => void {
    return () => undefined;
  }

  request(
    name: CommandName,
    _fields: CommandFields = {},
  ): Promise<ServerEvent> {
    if (name === "get_app_settings") {
      return Promise.resolve(FRESH_APP_SETTINGS_EVENT);
    }
    if (name === "get_settings") {
      return Promise.resolve(FRESH_EFFECTIVE_SETTINGS_EVENT);
    }
    if (name === "list_library") {
      return Promise.resolve(FRESH_LIBRARY_EVENT);
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

async function renderWithFreshDaemon(
  element: ReactElement,
): Promise<HTMLElement> {
  const container = document.createElement("div");
  document.body.append(container);
  const root = createRoot(container);
  roots.push(root);
  const client = new FreshDaemonClient();

  await act(async () => {
    root.render(
      <AppSettingsProvider client={client} connectionState="open">
        {element}
      </AppSettingsProvider>,
    );
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });

  return container;
}

function expectNoLoadError(container: HTMLElement): void {
  expect(container.textContent?.toLowerCase()).not.toContain(
    "could not be loaded",
  );
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
});

describe("fresh daemon payload regressions", () => {
  it("renders Appearance from the real app-settings shape", async () => {
    const container = await renderWithFreshDaemon(
      <Appearance connectionState="open" />,
    );

    expect(container.textContent).toContain("Theme");
    expect(container.textContent).toContain("Show Airy companion");
    expectNoLoadError(container);
  });

  it("renders Settings from the real app and effective-settings shapes", async () => {
    const client = new FreshDaemonClient();
    const container = await renderWithFreshDaemon(
      <Settings client={client} connectionState="open" />,
    );

    expect(container.textContent).toContain("Response");
    expect(container.textContent).toContain("Effective daemon configuration");
    expect(container.textContent).toContain("C:\\Users\\AirControl\\aircontrol.db");
    expectNoLoadError(container);
  });

  it("renders Calibration from the real settings and empty-library shapes", async () => {
    const client = new FreshDaemonClient();
    const container = await renderWithFreshDaemon(
      <Calibration client={client} connectionState="open" />,
    );

    expect(container.textContent).toContain("Active profile");
    expect(container.textContent).toContain("Not calibrated");
    expect(container.textContent).toContain("No custom gestures recorded yet");
    expectNoLoadError(container);
  });
});
