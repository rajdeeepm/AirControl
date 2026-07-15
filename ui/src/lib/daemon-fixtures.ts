import type {
  AppSettingsEvent,
  LibraryEvent,
  SettingsEvent,
} from "./types";

/** Payload returned by get_app_settings for a fresh local store. */
export const FRESH_APP_SETTINGS_EVENT = {
  v: 1,
  type: "app_settings",
  settings: {
    sensitivity: 50,
    smoothing: 64,
    cursor_speed: 1,
    dominant_hand: "right",
    theme: "system",
    airy_enabled: true,
  },
} satisfies AppSettingsEvent;

/** Payload returned by get_settings for an uncalibrated default daemon. */
export const FRESH_EFFECTIVE_SETTINGS_EVENT = {
  v: 1,
  type: "settings",
  payload: {
    clutch_mode: "wake_pose",
    gate_thresholds: {
      t1: 0,
      t2: 0,
      t3: 0,
    },
    camera_index: 0,
    store_db_path: "C:\\Users\\AirControl\\aircontrol.db",
    has_calibration_profile: false,
    camera_state: "off",
    camera_error: null,
  },
} satisfies SettingsEvent;

/** Payload returned by list_library for a fresh local store. */
export const FRESH_LIBRARY_EVENT = {
  v: 1,
  type: "library",
  gestures: [],
} satisfies LibraryEvent;
