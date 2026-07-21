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

export type CameraState = "off" | "starting" | "active" | "error";

export interface CameraEvent {
  v: 1;
  type: "camera";
  state: CameraState;
  camera_error: string | null;
  id?: string;
}

export type RecordingPhase =
  | "inactive"
  | "capturing"
  | "pending_take"
  | "saved"
  | "refused";

export interface RecordingOutcome {
  saved: boolean;
  reason: string;
  gesture_id: number | null;
  conflict_gesture_name: string | null;
}

/**
 * Live per-frame capture feedback during recording, derived from the
 * segmentation machine's state. Optional so events built before this field
 * existed (and test fixtures that omit it) remain valid.
 */
export type RecordingCaptureState =
  | "idle"
  | "searching"
  | "hand_present"
  | "in_motion"
  | "pending_take";

export interface RecordingEvent {
  v: 1;
  type: "recording";
  phase: RecordingPhase;
  name: string;
  takes_confirmed: number;
  min_takes: number;
  max_takes: number;
  pending_take: boolean;
  pending_take_frames: number | null;
  outcome: RecordingOutcome | null;
  capture_state?: RecordingCaptureState;
  id?: string;
}

export interface GestureAnimation {
  timestamps: number[];
  /** One entry per frame: 21 landmarks of [x, y, z]. */
  frames: [number, number, number][][];
}

export interface GestureMapping extends Record<string, unknown> {
  /** Authoritative persisted state; action fields live alongside this metadata. */
  enabled: boolean;
}

export interface LibraryGesture {
  id: number;
  name: string;
  description: string;
  exemplar_count: number;
  confirms: number;
  rejects: number;
  threshold_offset: number;
  mapping: GestureMapping | null;
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

export interface CalibrationSummary {
  hand_size: number;
  lighting_acceptable: boolean;
  created_at: number;
}

export interface EffectiveSettings extends Record<string, unknown> {
  has_calibration_profile: boolean;
  calibration?: CalibrationSummary;
  camera_state: CameraState;
  camera_error: string | null;
}

export interface SettingsEvent {
  v: 1;
  type: "settings";
  payload: EffectiveSettings;
  id?: string;
}

export type ThemePreference = "light" | "dark" | "system";
export type DominantHand = "left" | "right";
export type ClickMode = "single" | "two_hand";

export interface AdvancedAppSettings {
  pointer_responsiveness?: number;
  click_engage?: number;
  click_release?: number;
  pinch_approach?: number;
  pinch_drag_release?: number;
  arm_hold_seconds?: number;
  pause_hold_seconds?: number;
  scroll_speed?: number;
  swipe_distance?: number;
}

export interface AppSettings {
  sensitivity: number;
  smoothing: number;
  cursor_speed: number;
  dominant_hand: DominantHand;
  click_mode: ClickMode;
  theme: ThemePreference;
  airy_enabled: boolean;
  /**
   * Advanced numeric settings are optional for compatibility with older daemons.
   * The concrete supported keys are declared by AdvancedAppSettings.
   */
  [key: `${
    | "pointer"
    | "pinch"
    | "arm"
    | "pause"
    | "scroll"
    | "swipe"}_${string}`]: number | undefined;
  [key: `click_${string}`]: number | ClickMode | undefined;
  /** Optional for compatibility with daemons released before drag lock. */
  [key: `drag_lock_${string}`]: boolean | undefined;
}

export interface AppSettingsEvent {
  v: 1;
  type: "app_settings";
  settings: AppSettings & AdvancedAppSettings;
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
  | CameraEvent
  | RecordingEvent
  | LibraryEvent
  | MetricsSnapshotEvent
  | SettingsEvent
  | AppSettingsEvent
  | AckEvent
  | ProtocolErrorEvent;

export type ServerEventType = ServerEvent["type"];

export type RecordingCommandName =
  | "start_recording"
  | "confirm_take"
  | "discard_take"
  | "finish_recording"
  | "cancel_recording"
  | "get_recording_state";

export type CommandName =
  | RecordingCommandName
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
  | "reset_app_settings"
  | "set_camera"
  | "retry_camera"
  | "set_preview"
  | "delete_everything";

export interface CommandFields {
  gesture_id?: number;
  gesture_name?: string;
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
