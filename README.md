# AirControl

AirControl is a Windows-first prototype that turns a normal laptop webcam into an in-air touchpad. It tracks 21 hand landmarks locally, recognizes a deliberately small gesture vocabulary, and translates stable gestures into mouse, wheel, and window-management input.

The camera starts **disarmed**. No input is sent until you deliberately arm it with an open palm or `Space`.

## Try it safely

You need **x64 Windows 10/11** and **x64 Python 3.11 or newer** with the Python Launcher enabled. If Python is missing, setup prints the official download link and stops without changing system settings.

1. Double-click **`practice.cmd`**.
2. On the first run, setup installs the private dependencies and downloads the hand-tracking model.
3. Allow camera access if Windows asks.
4. Keep one palm facing the camera with the wrist and fingertips visible.
5. Practice mode displays the actions it would perform but never controls Windows.
6. Optionally double-click **`calibrate.cmd`** for the ~2-minute guided calibration (framing, hand size, motion speed, a 30-second "just work normally" capture, and a lighting check). The saved profile personalizes arming and motion thresholds.
7. When that feels reliable, close it and double-click **`start.cmd`** for live control.

Use `Q` to quit. `Space` toggles armed/paused while the preview window is focused. For 3 seconds after any fired action, `Esc` undoes it where reversible (switch back, reverse scroll, close Task View) and records it as a false positive; otherwise `Esc` quits. Closing the preview releases the camera and any held mouse button.

## Gesture map

| Gesture | Action |
|---|---|
| Hold an open palm for 0.7 seconds | Arm control |
| Hold a fist for 0.55 seconds | Pause and cancel the active gesture |
| Point with the index finger | Move the pointer like a relative touchpad |
| While pointing, touch thumb to index and keep the other fingers folded | Mouse button down |
| Release the pinch | Mouse button up; a quick pinch is a click, a moving pinch is a drag |
| Hold index + middle fingers up and move vertically | Two-finger natural scroll |
| Hold three fingers up and swipe left/right | Next/previous app |
| Hold three fingers up and swipe upward | Task View |
| Hold three fingers up and swipe downward | Show desktop |

Every active pose must remain stable for 140 ms before it takes ownership. Swipe distances and motion are normalized by palm size, so moving nearer to the camera should not multiply sensitivity. A three-finger swipe fires once and must be released before it can fire again.

## Privacy and safety

- Hand tracking runs on the laptop. AirControl does not upload, transmit, or save camera frames.
- Frame inference works offline after initial setup and the first hand-model download.
- On first launch, AirControl shows a required notice: MediaPipe 0.10.35 says camera/input data stays on-device, but it separately sends performance and API-utilization metrics to Google when a connection is available. AirControl starts only after explicit consent. The upstream notice is in [MediaPipe's privacy notice](https://github.com/google-ai-edge/mediapipe#privacy-notice).
- To withdraw that consent, delete `.aircontrol-consent.json`. AirControl will show the notice again and will not start unless consent is given again.
- Losing the hand releases a drag after a short grace period. A stalled camera or model triggers an independent watchdog and pauses control; keeping the hand away for three seconds also pauses it.
- Default commands are navigation-only; there are no delete, close, send, purchase, or shell gestures.
- Windows intentionally blocks normal applications from injecting input into administrator/elevated windows and secure screens. AirControl does not request elevation or work around that protection.
- `practice.cmd` is the recommended place to tune recognition before using live control.

## Tuning

Edit [`config.json`](config.json) and restart the app. The most useful settings are:

- `input.pointer_pixels_per_palm`: pointer speed.
- `gestures.pointer_smoothing`: lower is steadier, higher is more responsive.
- `gestures.natural_scroll`: set to `false` to reverse scrolling.
- `gestures.arm_hold_seconds` and `pause_hold_seconds`: activation dwell times.
- `gestures.swipe_threshold_palms`: how far a three-finger swipe must travel.
- `camera.index`: try `1` if the wrong camera opens.

## How it is structured

```text
Webcam + MediaPipe worker (latest frame only)
  → aspect-corrected image motion + world landmarks
  → relative-reach pose recognition (foreshortening-tolerant)
  → clutch strategy (wake pose by default) + temporal gesture state machine
  → landmark-trajectory buffer → calibrated segmentation → confidence gate
  → independent stall watchdog
  → safe action command (+ 3-second undo window)
  → Windows SendInput
```

Everything runs inside a daemon object with a loopback-only WebSocket boundary
(off by default) for the future settings UI. Calibration profiles, gesture
exemplars (landmark trajectories only — never video), and action mappings live
in a local SQLite store under your user profile, with a delete-everything
operation. Recognition and desktop control are separated, so the whole gesture
stack is tested with synthetic landmark sequences — no camera required.

## Development

Run `setup.cmd`, then:

```powershell
.venv\Scripts\python.exe -m pytest
.venv\Scripts\python.exe -m aircontrol --practice
```

The first release intentionally leaves out two-hand zoom, circular volume gestures, and depth “air taps.” A single webcam estimates depth noisily, and thumb–index pinch already owns click/drag; those features need a calibration and conflict-resolution pass rather than another single-frame rule.

## Current scope

This is a testable MVP, not yet a background utility. The next product pass should add a tray control, global pause hotkey, guided personal calibration, opt-in landmark replay capture, and negative-session testing before wider distribution.
