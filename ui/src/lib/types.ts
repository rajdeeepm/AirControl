/** TypeScript mirror of the daemon's v1 IPC schema (src/aircontrol/ipc.py). */

export const PROTOCOL_VERSION = 1;

export interface StatusEvent {
  v: 1;
  type: "status";
  armed: boolean;
  raw_pose: string;
  active_pose: string;
  hold_progress: number;
  hand_visible: boolean;
  status_text: string;
}

export interface ActionEvent {
  v: 1;
  type: "action";
  kind: string;
  confidence: number;
  description: string;
  ts: number;
}

export interface CandidateEvent {
  v: 1;
  type: "candidate";
  gate: "fire" | "abstain";
  reason: string;
  confidence: number;
  ts: number;
}

export interface MetricsEvent {
  v: 1;
  type: "metrics";
  candidates_per_hour: number;
  fp_per_hour: number;
  latency_ms_p50: number;
  cpu_pct: number;
  ts: number;
}

export interface GestureAnimation {
  timestamps: number[];
  /** One entry per frame: 21 landmarks of [x, y, z]. */
  frames: [number, number, number][][];
}

export interface LibraryGesture {
  id: number;
  name: string;
  description: string;
  exemplar_count: number;
  confirms: number;
  rejects: number;
  threshold_offset: number;
  mapping: Record<string, unknown> | null;
  animation: GestureAnimation | null;
}

export interface LibraryEvent {
  v: 1;
  type: "library";
  gestures: LibraryGesture[];
  id?: string;
}

export interface MetricsSnapshotEvent {
  v: 1;
  type: "metrics_snapshot";
  candidates_per_hour: number;
  armed_candidates_per_hour: number;
  fp_per_hour: number;
  latency_ms_p50: number;
  latency_ms_p95: number;
  cpu_pct: number;
  uptime_seconds: number;
  id?: string;
}

export interface SettingsEvent {
  v: 1;
  type: "settings";
  payload: Record<string, unknown>;
  id?: string;
}

export type ThemePreference = "light" | "dark" | "system";
export type DominantHand = "left" | "right";

export interface AppSettings {
  sensitivity: number;
  smoothing: number;
  cursor_speed: number;
  dominant_hand: DominantHand;
  theme: ThemePreference;
  airy_enabled: boolean;
}

export interface AppSettingsEvent {
  v: 1;
  type: "app_settings";
  settings: AppSettings;
  id?: string;
}

export interface AckEvent {
  v: 1;
  type: "ack";
  id?: string;
  ok: boolean;
  error: string;
}

export interface ProtocolErrorEvent {
  v: 1;
  type: "error";
  message: string;
}

export type ServerEvent =
  | StatusEvent
  | ActionEvent
  | CandidateEvent
  | MetricsEvent
  | LibraryEvent
  | MetricsSnapshotEvent
  | SettingsEvent
  | AppSettingsEvent
  | AckEvent
  | ProtocolErrorEvent;

export type ServerEventType = ServerEvent["type"];

export type CommandName =
  | "toggle_arm"
  | "pause"
  | "quit"
  | "get_status"
  | "refresh_matcher"
  | "undo"
  | "list_library"
  | "set_mapping"
  | "delete_gesture"
  | "rename_gesture"
  | "get_metrics"
  | "get_settings"
  | "get_app_settings"
  | "set_app_setting"
  | "set_preview"
  | "delete_everything";

export interface CommandFields {
  gesture_id?: number;
  new_name?: string;
  action?: Record<string, unknown>;
  context?: string;
  enabled?: boolean;
  key?: string;
  value?: unknown;
}

export interface Command extends CommandFields {
  v: 1;
  type: "command";
  name: CommandName;
  id?: string;
}
