import { describe, expect, it, vi } from "vitest";
import { AirControlClient } from "./ws";

class MockSocket {
  sent: string[] = [];
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onmessage: ((message: { data: unknown }) => void) | null = null;

  send(data: string): void {
    this.sent.push(data);
  }

  close(): void {
    this.onclose?.();
  }

  emit(event: object): void {
    this.onmessage?.({ data: JSON.stringify(event) });
  }
}

function connectedClient(): { client: AirControlClient; socket: MockSocket } {
  let socket: MockSocket | null = null;
  const client = new AirControlClient(
    "ws://127.0.0.1:8787",
    () => {
      socket = new MockSocket();
      return socket;
    },
    200,
  );
  client.connect();
  if (socket === null) throw new Error("factory not called");
  (socket as MockSocket).onopen?.();
  return { client, socket };
}

describe("AirControlClient", () => {
  it("reports connection state transitions", () => {
    const states: string[] = [];
    let socket: MockSocket | null = null;
    const client = new AirControlClient("ws://x", () => {
      socket = new MockSocket();
      return socket;
    });
    client.onState((s) => states.push(s));
    client.connect();
    (socket as unknown as MockSocket).onopen?.();
    expect(states).toEqual(["closed", "connecting", "open"]);
  });

  it("dispatches typed events to subscribers", () => {
    const { client, socket } = connectedClient();
    const seen: unknown[] = [];
    client.on("status", (event) => seen.push(event));
    socket.emit({
      v: 1,
      type: "status",
      armed: true,
      raw_pose: "Open palm",
      active_pose: "Neutral",
      hold_progress: 1,
      hand_visible: true,
      status_text: "Armed",
    });
    socket.emit({ v: 1, type: "metrics", ts: 0 });
    expect(seen).toHaveLength(1);
    expect((seen[0] as { armed: boolean }).armed).toBe(true);
  });

  it("correlates request replies by id", async () => {
    const { client, socket } = connectedClient();
    const promise = client.request("list_library");
    const sent = JSON.parse(socket.sent[0]);
    expect(sent.name).toBe("list_library");
    expect(typeof sent.id).toBe("string");
    socket.emit({ v: 1, type: "library", gestures: [], id: sent.id });
    const reply = await promise;
    expect(reply.type).toBe("library");
  });

  it("times out unanswered requests", async () => {
    vi.useFakeTimers();
    try {
      const { client } = connectedClient();
      const promise = client.request("get_metrics");
      const assertion = expect(promise).rejects.toThrow(/timed out/);
      vi.advanceTimersByTime(250);
      await assertion;
    } finally {
      vi.useRealTimers();
    }
  });

  it("ignores malformed and wrong-version messages", () => {
    const { client, socket } = connectedClient();
    const seen: unknown[] = [];
    client.on("status", (event) => seen.push(event));
    socket.onmessage?.({ data: "not json" });
    socket.emit({ v: 2, type: "status" });
    expect(seen).toHaveLength(0);
  });

  it("sends fire-and-forget commands with fields", () => {
    const { client, socket } = connectedClient();
    client.send("rename_gesture", { gesture_id: 3, new_name: "Wave" });
    const sent = JSON.parse(socket.sent[0]);
    expect(sent).toMatchObject({
      v: 1,
      type: "command",
      name: "rename_gesture",
      gesture_id: 3,
      new_name: "Wave",
    });
  });
});
