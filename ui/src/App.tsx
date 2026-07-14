import { useEffect, useRef, useState } from "react";
import { AirControlClient, ConnectionState } from "./lib/ws";
import { Library } from "./screens/Library";
import { Mappings } from "./screens/Mappings";
import { Settings } from "./screens/Settings";
import { Status } from "./screens/Status";

const SCREENS = ["Status", "Library", "Mappings", "Settings"] as const;

type Screen = (typeof SCREENS)[number];

function connectionLabel(state: ConnectionState): string {
  if (state === "open") return "Connected";
  if (state === "connecting") return "Connecting";
  return "Disconnected";
}

export default function App() {
  const clientRef = useRef<AirControlClient | null>(null);
  if (clientRef.current === null) {
    clientRef.current = new AirControlClient("ws://127.0.0.1:8787");
  }
  const client = clientRef.current;

  const [screen, setScreen] = useState<Screen>("Status");
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

  let activeScreen: React.ReactNode;
  switch (screen) {
    case "Library":
      activeScreen = (
        <Library client={client} connectionState={connectionState} />
      );
      break;
    case "Mappings":
      activeScreen = (
        <Mappings client={client} connectionState={connectionState} />
      );
      break;
    case "Settings":
      activeScreen = (
        <Settings client={client} connectionState={connectionState} />
      );
      break;
    case "Status":
      activeScreen = <Status client={client} connectionState={connectionState} />;
      break;
  }

  return (
    <div className="shell">
      <nav className="nav" aria-label="Primary navigation">
        <div className="brand">AirControl</div>
        <div className="nav-links">
          {SCREENS.map((item) => (
            <button
              key={item}
              type="button"
              aria-current={screen === item ? "page" : undefined}
              onClick={() => setScreen(item)}
            >
              {item}
            </button>
          ))}
        </div>
        <div
          className="conn"
          data-state={connectionState}
          aria-live="polite"
        >
          <span className="dot" aria-hidden="true" />
          {connectionLabel(connectionState)}
        </div>
      </nav>
      <main className="content">{activeScreen}</main>
    </div>
  );
}
