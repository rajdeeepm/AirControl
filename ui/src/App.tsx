import { useEffect, useRef, useState, type ReactNode } from "react";

import { AppSettingsProvider } from "./lib/app-settings";
import { APP_NAME, APP_VERSION } from "./lib/constants";
import { AirControlClient, type ConnectionState } from "./lib/ws";
import { About } from "./screens/About";
import { Appearance } from "./screens/Appearance";
import { Calibration } from "./screens/Calibration";
import { Dashboard } from "./screens/Dashboard";
import { Gestures } from "./screens/Gestures";
import { Settings } from "./screens/Settings";

const SCREENS = [
  "Dashboard",
  "Gestures",
  "Settings",
  "Calibration",
  "Appearance",
  "About",
] as const;

type Screen = (typeof SCREENS)[number];

function connectionLabel(state: ConnectionState): string {
  if (state === "open") return "Daemon connected";
  if (state === "connecting") return "Connecting to daemon";
  return "Daemon disconnected";
}

function NavIcon({ screen }: { screen: Screen }) {
  const common = {
    width: 20,
    height: 20,
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.8,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    "aria-hidden": true,
  };

  switch (screen) {
    case "Dashboard":
      return (
        <svg {...common}>
          <rect x="3" y="3" width="8" height="8" rx="1.5" />
          <rect x="13" y="3" width="8" height="5" rx="1.5" />
          <rect x="13" y="10" width="8" height="11" rx="1.5" />
          <rect x="3" y="13" width="8" height="8" rx="1.5" />
        </svg>
      );
    case "Gestures":
      return (
        <svg {...common}>
          <path d="M7.7 11.2V5.5a1.45 1.45 0 0 1 2.9 0v4.1" />
          <path d="M10.6 9V3.9a1.45 1.45 0 1 1 2.9 0V9" />
          <path d="M13.5 9.1V5a1.45 1.45 0 0 1 2.9 0v5.4" />
          <path d="M16.4 10V7.3a1.45 1.45 0 0 1 2.9 0v6.2c0 5-2.6 7.5-6.9 7.5-3.6 0-5.6-1.5-7.4-4.7l-1.4-2.5a1.6 1.6 0 0 1 2.7-1.7l1.4 1.8" />
        </svg>
      );
    case "Settings":
      return (
        <svg {...common}>
          <path d="M4 6h10" />
          <path d="M18 6h2" />
          <circle cx="16" cy="6" r="2" />
          <path d="M4 12h2" />
          <path d="M10 12h10" />
          <circle cx="8" cy="12" r="2" />
          <path d="M4 18h7" />
          <path d="M15 18h5" />
          <circle cx="13" cy="18" r="2" />
        </svg>
      );
    case "Calibration":
      return (
        <svg {...common}>
          <circle cx="12" cy="12" r="8" />
          <circle cx="12" cy="12" r="3" />
          <path d="M12 2v3M12 19v3M2 12h3M19 12h3" />
        </svg>
      );
    case "Appearance":
      return (
        <svg {...common}>
          <circle cx="12" cy="12" r="4" />
          <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
        </svg>
      );
    case "About":
      return (
        <svg {...common}>
          <circle cx="12" cy="12" r="9" />
          <path d="M12 11v6" />
          <path d="M12 7.2h.01" />
        </svg>
      );
  }
}

function BrandMark() {
  return (
    <svg
      className="brand-mark"
      width="30"
      height="30"
      viewBox="0 0 30 30"
      role="img"
      aria-label="AirControl logo"
    >
      <rect width="30" height="30" rx="8" fill="currentColor" />
      <path
        d="M8.5 20.5 13.1 8.8h3.8l4.6 11.7h-3.2l-.9-2.5h-5l-.9 2.5h-3Zm4.8-5h3.2l-1.6-4.6-1.6 4.6Z"
        fill="var(--accent-ink)"
      />
    </svg>
  );
}

function activeScreen(
  screen: Screen,
  client: AirControlClient,
  connectionState: ConnectionState,
  navigate: (screen: Screen) => void,
): ReactNode {
  switch (screen) {
    case "Dashboard":
      return (
        <Dashboard
          client={client}
          connectionState={connectionState}
          onNavigate={navigate}
        />
      );
    case "Gestures":
      return <Gestures client={client} connectionState={connectionState} />;
    case "Settings":
      return <Settings client={client} connectionState={connectionState} />;
    case "Calibration":
      return <Calibration client={client} connectionState={connectionState} />;
    case "Appearance":
      return <Appearance connectionState={connectionState} />;
    case "About":
      return <About connectionState={connectionState} />;
  }
}

export default function App() {
  const clientRef = useRef<AirControlClient | null>(null);
  if (clientRef.current === null) {
    clientRef.current = new AirControlClient("ws://127.0.0.1:8787");
  }
  const client = clientRef.current;

  const [screen, setScreen] = useState<Screen>("Dashboard");
  const [connectionState, setConnectionState] = useState<ConnectionState>(
    client.state,
  );

  useEffect(() => {
    const unsubscribe = client.onState(setConnectionState);
    client.connect();
    return () => {
      unsubscribe();
      client.close();
    };
  }, [client]);

  return (
    <AppSettingsProvider client={client} connectionState={connectionState}>
      <a className="skip-link" href="#main-content">
        Skip to main content
      </a>
      <div className="app-shell">
        <aside className="sidebar">
          <div className="brand-lockup">
            <BrandMark />
            <span>{APP_NAME}</span>
          </div>

          <nav className="primary-nav" aria-label="Primary navigation">
            {SCREENS.map((item) => (
              <button
                key={item}
                type="button"
                aria-current={screen === item ? "page" : undefined}
                onClick={() => setScreen(item)}
              >
                <NavIcon screen={item} />
                <span>{item}</span>
              </button>
            ))}
          </nav>

          <footer className="sidebar-footer">
            <div
              className="connection-state"
              data-state={connectionState}
              aria-live="polite"
            >
              <span className="connection-dot" aria-hidden="true" />
              <span>{connectionLabel(connectionState)}</span>
            </div>
            <span className="app-version">Version {APP_VERSION}</span>
          </footer>
        </aside>

        <main id="main-content" className="main-content" tabIndex={-1}>
          <div className="page-canvas">
            {activeScreen(screen, client, connectionState, setScreen)}
          </div>
        </main>
      </div>
    </AppSettingsProvider>
  );
}
