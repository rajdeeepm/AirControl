import {
  Command,
  CommandFields,
  CommandName,
  ServerEvent,
  ServerEventType,
} from "./types";

export type ConnectionState = "connecting" | "open" | "closed";

type EventCallback = (event: ServerEvent) => void;
type StateCallback = (state: ConnectionState) => void;

interface WebSocketLike {
  send(data: string): void;
  close(): void;
  onopen: (() => void) | null;
  onclose: (() => void) | null;
  onerror: (() => void) | null;
  onmessage: ((message: { data: unknown }) => void) | null;
}

export type WebSocketFactory = (url: string) => WebSocketLike;

const REQUEST_TIMEOUT_MS = 5_000;
const RECONNECT_BASE_MS = 500;
const RECONNECT_MAX_MS = 8_000;

/** Typed client for the daemon's loopback WebSocket. */
export class AirControlClient {
  private socket: WebSocketLike | null = null;
  private listeners = new Map<ServerEventType, Set<EventCallback>>();
  private stateListeners = new Set<StateCallback>();
  private pending = new Map<
    string,
    { resolve: (event: ServerEvent) => void; timer: ReturnType<typeof setTimeout> }
  >();
  private reconnectAttempts = 0;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private closedByUser = false;
  private nextRequestId = 1;
  state: ConnectionState = "closed";

  constructor(
    private url: string,
    private factory: WebSocketFactory = (u) =>
      new WebSocket(u) as unknown as WebSocketLike,
    private requestTimeoutMs: number = REQUEST_TIMEOUT_MS,
  ) {}

  connect(): void {
    this.closedByUser = false;
    this.open();
  }

  close(): void {
    this.closedByUser = true;
    if (this.reconnectTimer !== null) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this.socket?.close();
    this.setState("closed");
  }

  /** Fire-and-forget command (no correlation id). */
  send(name: CommandName, fields: CommandFields = {}): void {
    const command: Command = { v: 1, type: "command", name, ...fields };
    this.socket?.send(JSON.stringify(command));
  }

  /** Command with a correlation id; resolves with the reply event. */
  request(name: CommandName, fields: CommandFields = {}): Promise<ServerEvent> {
    const id = `req-${this.nextRequestId++}`;
    const command: Command = { v: 1, type: "command", name, id, ...fields };
    return new Promise((resolve, reject) => {
      if (this.state !== "open" || this.socket === null) {
        reject(new Error("not connected"));
        return;
      }
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`request ${name} timed out`));
      }, this.requestTimeoutMs);
      this.pending.set(id, { resolve, timer });
      this.socket.send(JSON.stringify(command));
    });
  }

  on(type: ServerEventType, callback: EventCallback): () => void {
    let set = this.listeners.get(type);
    if (set === undefined) {
      set = new Set();
      this.listeners.set(type, set);
    }
    set.add(callback);
    return () => set.delete(callback);
  }

  onState(callback: StateCallback): () => void {
    this.stateListeners.add(callback);
    callback(this.state);
    return () => this.stateListeners.delete(callback);
  }

  private open(): void {
    this.setState("connecting");
    const socket = this.factory(this.url);
    this.socket = socket;
    socket.onopen = () => {
      this.reconnectAttempts = 0;
      this.setState("open");
    };
    socket.onmessage = (message) => this.handleMessage(message.data);
    socket.onerror = () => {
      /* onclose always follows */
    };
    socket.onclose = () => {
      this.socket = null;
      this.setState("closed");
      this.failPending(new Error("connection closed"));
      if (!this.closedByUser) {
        this.scheduleReconnect();
      }
    };
  }

  private scheduleReconnect(): void {
    const delay = Math.min(
      RECONNECT_BASE_MS * 2 ** this.reconnectAttempts,
      RECONNECT_MAX_MS,
    );
    this.reconnectAttempts += 1;
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      if (!this.closedByUser) {
        this.open();
      }
    }, delay);
  }

  private handleMessage(data: unknown): void {
    if (typeof data !== "string") {
      return;
    }
    let event: ServerEvent;
    try {
      event = JSON.parse(data) as ServerEvent;
    } catch {
      return;
    }
    if (event.v !== 1 || typeof event.type !== "string") {
      return;
    }
    const id = (event as { id?: string }).id;
    if (id !== undefined) {
      const waiter = this.pending.get(id);
      if (waiter !== undefined) {
        this.pending.delete(id);
        clearTimeout(waiter.timer);
        waiter.resolve(event);
        return;
      }
    }
    const callbacks = this.listeners.get(event.type);
    callbacks?.forEach((callback) => callback(event));
  }

  private failPending(error: Error): void {
    for (const [, waiter] of this.pending) {
      clearTimeout(waiter.timer);
    }
    this.pending.clear();
    void error;
  }

  private setState(state: ConnectionState): void {
    if (this.state === state) {
      return;
    }
    this.state = state;
    this.stateListeners.forEach((callback) => callback(state));
  }
}
