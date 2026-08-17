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

/**
 * Whether a custom gesture is a moving trajectory ("motion", the original
 * kind) or a held hand shape ("pose"). Optional on events built before this
 * field existed (and test fixtures that omit it); callers should treat a
 * missing value as "motion".
 */
export type GestureKind = "motion" | "pose";

export interface RecordingOutcome {
  saved: boolean;
  reason: string;
  gesture_id: number | null;
  conflict_gesture_name: string | null;
}

/**
 * Live capture feedback during recording. Press-to-start / press-to-stop
 * model: "idle" waits for the user to press start, "capturing" is an open
 * capture window the user must explicitly stop, "pending_take" awaits
 * keep/discard. Optional so events built before this field existed (and
 * test fixtures that omit it) remain valid.
 */
export type RecordingCaptureState = "idle" | "capturing" | "pending_take";

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
  /** Safety cap: a take auto-finalises if never explicitly stopped by this long. */
  max_take_seconds?: number;
  /** Seconds since the open capture window began; seeds the UI's own live timer. */
  capture_elapsed_seconds?: number;
  /**
   * Set when the last capture window produced no take: "no motion" (motion
   * gestures), or "pose unstable" / "pose too similar to built-in" (pose
   * gestures).
   */
  last_take_refused?: string | null;
  /** Whether this recording is a moving gesture or a held hand pose. */
  gesture_kind?: GestureKind;
  /**
   * Live "is the held pose steady right now" signal while capturing a pose
   * take. ``null``/undefined for motion recordings, or before enough frames
   * have arrived to judge steadiness.
   */
  pose_steady?: boolean | null;
  id?: string;
}

/**
 * Live status ("no_hand" / "moving" / "holding") or a match attempt
 * ("attempt") from the daemon's mandatory "test your gesture" step. Status
 * ticks are throttled to changes server-side; attempts are always sent.
 */
export type GestureTestState = "no_hand" | "moving" | "holding" | "attempt";

export interface GestureTestEvent {
  v: 1;
  type: "gesture_test";
  state: GestureTestState;
  /** Only meaningful for state "attempt"; null otherwise or on no/low match. */
  matched_gesture_id: number | null;
  /** Top-1 similarity (0..1) for an "attempt"; 0 for status ticks. */
  confidence: number;
  /** Top-2 (runner-up) similarity, for diagnosing near-misses. */
  runner_up: number;
  /** Whether this attempt really fired -- the matched gesture's mapped
   * action was actually dispatched, exactly as live use would. */
  fired: boolean;
  /** Whether matched_gesture_id is the gesture under test. */
  is_target: boolean;
  /** Gate/floor reason string, e.g. "ok", "t1", "below_min_confidence". */
  reason: string;
  /** The hard similarity floor a match must clear to ever fire. */
  min_confidence: number;
  /** The dispatched action's human-readable description when ``fired`` is
   * true (e.g. "NEXT APP"), otherwise null. The UI shows "Performed: <this>". */
  action_description?: string | null;
  ts: number;
  id?: string;
}

/**
 * The guided calibration flow's step names, in order. "complete" is the
 * terminal step reported alongside ``complete: true``. Empty string when no
 * calibration session is active.
 */
export type CalibrationStep =
  | ""
  | "framing"
  | "hand_snapshot"
  | "motion_signature"
  | "negative_capture"
  | "lighting"
  | "complete";

/**
 * Live progress for the in-app guided calibration flow. ``active`` is true
 * while a session is running. ``recording`` mirrors whether the current step
 * is actively sampling right now (vs. waiting for the user to press
 * Continue). ``complete`` is true exactly once, on the event reporting the
 * finished (and persisted) profile. ``error`` carries a reason when starting
 * or finishing calibration failed.
 */
export interface CalibrationEvent {
  v: 1;
  type: "calibration";
  active: boolean;
  step: CalibrationStep;
  instruction: string;
  progress: number;
  recording: boolean;
  complete: boolean;
  error: string | null;
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
  /** Optional for compatibility with daemons released before pose gestures. */
  kind?: GestureKind;
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
  | ProtocolErrorEvent
  | GestureTestEvent
  | CalibrationEvent;

export type ServerEventType = ServerEvent["type"];

export type RecordingCommandName =
  | "start_recording"
  | "start_take"
  | "end_take"
  | "confirm_take"
  | "discard_take"
  | "finish_recording"
  | "cancel_recording"
  | "get_recording_state";

export type GestureTestCommandName = "start_gesture_test" | "stop_gesture_test";

export type CalibrationCommandName =
  | "start_calibration"
  | "advance_calibration"
  | "cancel_calibration"
  | "get_calibration_state";

export type CommandName =
  | RecordingCommandName
  | GestureTestCommandName
  | CalibrationCommandName
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
  gesture_kind?: GestureKind;
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
