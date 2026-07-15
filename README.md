# AirControl

## Install

Download **`AirControl-Setup.exe`** from the [Releases page](https://github.com/rajdeeepm/AirControl/releases), run the installer, then launch **AirControl** from the Windows Start menu. No Python installation or command-line setup is required.

The `.cmd` scripts in this repository are developer tools for building and running AirControl from source. End users should install the released `AirControl-Setup.exe` instead.

[![CI](https://github.com/rajdeeepm/AirControl/actions/workflows/ci.yml/badge.svg)](https://github.com/rajdeeepm/AirControl/actions/workflows/ci.yml)

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

## Desktop app and Airy companion

AirControl includes a native desktop application window with six screens: **Dashboard** centers the live camera preview, tracking status, controls, mappings summary, and quick start; **Gestures** manages recorded gestures and action mappings; **Settings** controls recognition, pointer behavior, handedness, clutch mode, and privacy; **Calibration** reports active-profile truth and the guided recalibration steps; **Appearance** selects light, dark, or system theme; and **About** provides version, guide, and privacy information. The window hosts the built local UI with `pywebview`; it does not open a browser tab.

Build the UI once from the AirControl folder:

```cmd
npm --prefix ui install && npm --prefix ui run build
```

Then double-click **`app.cmd`**. It opens the real desktop app in live mode and, when enabled in app settings, creates **Airy** in the same desktop session. Live control still begins disarmed and requires a deliberate open-palm hold before AirControl can send input. To open the desktop app without real input, run `python -m aircontrol --app --practice` instead.

Airy is the frameless, always-on-top companion widget. It reconnects to the daemon automatically and always names its state in text:

- **Active** — connected and armed, with the sub-line **Tracking your gestures**.
- **Inactive** — connected but paused, with the sub-line **Gesture tracking paused**.
- **Offline** — AirControl is unreachable, with the sub-line **AirControl is not running**.

Click Airy to arm or pause gesture tracking. Drag the companion to reposition it; its position is restored the next time it opens. Click the small **x** to hide/close it. Airy can also run by itself with `python -m aircontrol --widget`.

| Launcher | Purpose |
|---|---|
| `practice.cmd` | Recognize gestures without controlling Windows |
| `start.cmd` | Run live desktop control |
| `calibrate.cmd` | Run the guided personal calibration |
| `record.cmd` | Record a custom gesture (optionally pass its name) |
| `arena.cmd` | Practice recorded gestures; pass `stress` for the false-fire test |
| `app.cmd` | Open the live native desktop app with Airy in the same WebView session |

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

- Hand tracking runs on the laptop. AirControl never uploads or saves camera frames.
- While the app preview is enabled, only its annotated JPEG frames travel in RAM over the `127.0.0.1` loopback socket. They never leave the computer or get written to disk; raw camera frames are not streamed.
- Frame inference works offline after initial setup and the first hand-model download.
- On first launch, AirControl shows a required notice: MediaPipe 0.10.35 says camera/input data stays on-device, but it separately sends performance and API-utilization metrics to Google when a connection is available. AirControl starts only after explicit consent. The upstream notice is in [MediaPipe's privacy notice](https://github.com/google-ai-edge/mediapipe#privacy-notice).
- To withdraw that consent from an installed app, delete `%LOCALAPPDATA%\AirControl\.aircontrol-consent.json`. For a source build, delete `.aircontrol-consent.json` beside the active config file. AirControl will show the notice again and will not start unless consent is given again.
- Losing the hand releases a drag after a short grace period. A stalled camera or model triggers an independent watchdog and pauses control; keeping the hand away for three seconds also pauses it.
- Default commands are navigation-only; there are no delete, close, send, purchase, or shell gestures.
- Windows intentionally blocks normal applications from injecting input into administrator/elevated windows and secure screens. AirControl does not request elevation or work around that protection.
- `practice.cmd` is the recommended place to tune recognition before using live control.

## Tuning

For an installed app, edit `%LOCALAPPDATA%\AirControl\config.json` and restart AirControl. For a source build, edit the repository [`config.json`](config.json). The most useful settings are:

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
(off by default and enabled by `--serve`, `--app`, and `app.cmd`) for the local app UI and Airy. Calibration
profiles, gesture exemplars (landmark trajectories only — never video), and
action mappings live in a local SQLite store under your user profile, with a
delete-everything operation. Recognition and desktop control are separated, so
the whole gesture stack is tested with synthetic landmark sequences — no camera
required.

## Development

Run `setup.cmd`, then:

```powershell
.venv\Scripts\python.exe -m pytest
.venv\Scripts\python.exe -m aircontrol --practice
```

### Build the installer yourself

Use Python 3.11 on Windows, then build the UI, the PyInstaller application directory, and the Inno Setup installer:

```powershell
npm --prefix ui install
npm --prefix ui run build
py -3.11 -m pip install -e . pyinstaller
py -3.11 -c "from aircontrol.model import ensure_hand_model; ensure_hand_model('models/hand_landmarker.task')"
py -3.11 -m PyInstaller packaging/aircontrol.spec
& "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe" packaging\installer.iss
```

Keep `#define AppVersion` in `packaging/installer.iss` synchronized with `project.version` in `pyproject.toml` whenever the release version changes.

The first release intentionally leaves out two-hand zoom, circular volume gestures, and depth “air taps.” A single webcam estimates depth noisily, and thumb–index pinch already owns click/drag; those features need a calibration and conflict-resolution pass rather than another single-frame rule.

## Current scope

This is a testable MVP with the Airy companion, not yet a full tray utility. A future product pass should add tray controls, a global pause hotkey, and broader negative-session testing before wider distribution.
