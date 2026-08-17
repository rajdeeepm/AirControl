export const APP_NAME = "AirControl";
export const APP_VERSION = "1.0.0";

export const BUILT_IN_GESTURES = [
  {
    gesture: "Open palm hold",
    action: "Arm AirControl",
    guidance: "Hold an open palm to make gesture actions available.",
  },
  {
    gesture: "Fist hold",
    action: "Pause AirControl",
    guidance: "Hold a fist to return to the safe, idle state.",
  },
  {
    gesture: "Index point",
    action: "Move pointer",
    guidance: "Point deliberately to guide the desktop pointer.",
  },
  {
    gesture: "Thumb + index pinch",
    action: "Click or drag",
    guidance: "Pinch once to click; hold the pinch while moving to drag.",
  },
  {
    gesture: "Two fingers",
    action: "Scroll",
    guidance: "Use two extended fingers and move vertically to scroll.",
  },
  {
    gesture: "Three-finger swipe left / right",
    action: "Next / previous app",
    guidance: "Swipe horizontally to move between open applications.",
  },
  {
    gesture: "Three-finger swipe up",
    action: "Task view",
    guidance: "Swipe upward to open Windows Task View.",
  },
  {
    gesture: "Three-finger swipe down",
    action: "Show desktop",
    guidance: "Swipe downward to reveal the desktop.",
  },
] as const;
