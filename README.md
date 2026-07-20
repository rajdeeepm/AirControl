# AirControl

[![CI](https://github.com/rajdeeepm/AirControl/actions/workflows/ci.yml/badge.svg)](https://github.com/rajdeeepm/AirControl/actions/workflows/ci.yml)

AirControl turns an ordinary laptop webcam into an in-air pointing device for
Windows. It watches your hand, recognizes a small, deliberate set of gestures,
and translates them into mouse movement, clicks, scrolling, and window
management.

**Privacy first.** All hand tracking runs on your own computer. AirControl works
from the 21 hand-skeleton landmarks that MediaPipe extracts locally — it never
uploads, streams, or saves camera video. Nothing about your camera leaves the
machine.

The camera always starts **disarmed**. No input is sent to Windows until you
deliberately arm control with an open-palm hold.

## What you need

- **x64 Windows 10 or 11.**
- A working webcam.
- For a source build only: **x64 Python 3.11 or newer** with the Python
  Launcher, and **Node.js** (for building the desktop UI).

## Install

### Option A — installer (recommended)

Once releases are published, download **`AirControl-Setup.exe`** from the
[Releases page](https://github.com/rajdeeepm/AirControl/releases), run it, and
launch **AirControl** from the Windows Start menu. No Python, Node, or
command-line setup is required.

### Option B — build from source

From the project folder on Windows:

1. Double-click **`setup.cmd`** (or run it in a terminal). It creates a private
   Python environment, installs AirControl, and downloads the hand-tracking
   model. If Python is missing or the wrong architecture, it prints the official
   download link and stops without changing system settings.
2. Build the desktop UI once:

   ```cmd
   npm --prefix ui install && npm --prefix ui run build
   ```

3. Double-click **`app.cmd`** to open the AirControl desktop app.

## Getting started

1. **Open the app.** Run `app.cmd`. It opens the native desktop window (hosted
   with `pywebview`, not a browser tab) and, when enabled in settings, the Airy
   companion in the same session. Control begins disarmed.
2. **Calibrate.** Run `calibrate.cmd`, or open the **Calibration** screen and
   follow the guided flow. Calibration personalizes arming and motion
   thresholds through these steps:
   - **Framing** — position your hand so the wrist and fingertips stay in view.
   - **Hand size** — measure your normalized hand size at a comfortable distance.
   - **Motion speed** — sample how fast you naturally move.
   - **Normal-work capture** — a ~30-second recording of you simply working as
     usual, so incidental desk motion is learned and later rejected.
   - **Lighting check** — confirm the scene is bright enough for stable tracking.

   The saved profile is your active calibration.
3. **Arm control.** With your dominant hand, hold an **open palm** facing the
   camera until it arms.
4. **Use gestures.** Move the pointer, click, scroll, and manage windows as
   described below. Hold a **fist** to pause.

If you want to try recognition without touching Windows, run `practice.cmd`
(or open the app with practice mode). Practice mode shows the actions it *would*
perform but never sends real input.

## Interaction model

AirControl supports one- and two-hand interaction.

### Single-hand mode

| Gesture | Action |
|---|---|
| Hold an **open palm** (dominant hand) | Arm control |
| Hold a **fist** | Pause and cancel the active gesture |
| **Point** with the index finger | Move the pointer like a relative touchpad |
| **Pinch** thumb to index (other fingers folded) | Mouse button down; the pointer freezes as the pinch approaches so the click lands where you aimed |
| **Release** the pinch | Mouse button up — a quick pinch is a click, a moving pinch is a drag |
| **Two fingers** (index + middle) moved vertically | Two-finger scroll |
| **Three-finger swipe left / right** | Next / previous app |
| **Three-finger swipe up** | Task View |
| **Three-finger swipe down** | Show desktop |

Every active pose must stay stable for a short dwell before it takes ownership,
and swipe distances are normalized by hand size, so moving closer to the camera
does not multiply sensitivity. A three-finger swipe fires once and must be
released before it can fire again.

### Two-hand mode (modifier design)

Two-hand mode makes clicks and drags explicit and deliberate. The **dominant
hand** always points and does **all** the pinching. The **non-dominant hand** is
a *modifier* that only chooses what the dominant pinch does:

| Non-dominant hand | Dominant pinch behaves as |
|---|---|
| **Open palm** | Cursor **locks** where the dominant hand is pointing, and the pinch **left-clicks** at that exact spot |
| **Fist** | Pinch **holds** the button so movement **drags** |
| No modifier (neutral) | The pointer keeps moving, and the pinch does **nothing** — so a click never happens by accident |

Only the **dominant hand's fist** disarms control; the modifier hand never
reaches the clutch.

## Custom gestures

You can record your own motions and map them to actions.

1. Open the **Gestures** screen and choose **+ Add Gesture** (or run
   `record.cmd`, optionally passing a name).
2. **Record takes.** Perform the motion several times; you review each take and
   **Keep** or **Discard** it. At least **8** kept takes are required (up to
   **12**).
3. **Blocking checks.** When you save, AirControl runs consistency,
   confusability, and desk-motion checks and refuses with an honest reason if the
   gesture is not reliable, for example:
   - *"Your takes were too inconsistent — try again."*
   - *"Too similar to \<gesture\> — make this motion more distinct."*
   - *"This looks like normal desk motion — make it more distinct."*
   - *"Record a few more takes before saving."*
4. **Map it.** A recorded gesture does nothing until you map it. On the Gestures
   screen, assign an action — either a **curated verb** grouped by category
   (Media, Windows, Reading and browser, and more), or use the **shortcut
   recorder**, which captures any key combination you press and turns it into a
   hotkey action.

Feedback while you record and use gestures comes from **Airy**, the companion
widget, which names its state in text (see below).

### Tuning

The **Settings** screen exposes the everyday **Response** controls (pointer
responsiveness, dominant hand, click mode). The **Advanced tuning** screen adds
finer sliders — click engage/release distance, pinch approach distance, drag
lock and release motion, arm and pause hold times, scroll speed, and swipe
distance — plus a **Reset** that restores every app setting to its defaults. The
**Calibration** screen reports the active profile and lets you recalibrate.

## The Airy companion

Airy is a frameless, always-on-top widget that reconnects to AirControl
automatically and always names its state:

- **Active** — connected and armed (*"Tracking your gestures"*).
- **Inactive** — connected but paused (*"Gesture tracking paused"*).
- **Offline** — AirControl is unreachable (*"AirControl is not running"*).

Click Airy to arm or pause. Drag it to reposition (its position is remembered),
and click the small **x** to hide it. Airy can also run on its own with
`python -m aircontrol --widget`.

## Launchers

The `.cmd` files are developer conveniences for running from source. Each one
bootstraps the environment via `setup.cmd` on first use.

| Launcher | Purpose |
|---|---|
| `setup.cmd` | Create the private Python environment and install AirControl |
| `app.cmd` | Open the live native desktop app (with Airy in the same session) |
| `start.cmd` | Run live desktop control without the app window |
| `practice.cmd` | Recognize gestures without controlling Windows |
| `calibrate.cmd` | Run the guided personal calibration |
| `record.cmd` | Record a custom gesture (optionally pass its name) |
| `arena.cmd` | Practice recorded gestures; pass `stress` for the false-fire test |

To open the desktop app without sending real input, run
`python -m aircontrol --app --practice`.

## Privacy and safety

- Hand tracking runs entirely on your computer. AirControl never uploads or
  saves camera frames, and works offline after the initial model download.
- Only hand-skeleton landmarks — never video — drive recognition. When the app
  preview is enabled, only annotated JPEG frames travel in RAM over the
  `127.0.0.1` loopback socket for the local UI; they are never written to disk or
  sent off the machine.
- Calibration profiles, gesture exemplars (landmark trajectories only), and
  action mappings live in a local SQLite store under
  `%LOCALAPPDATA%\AirControl`. A **delete-everything** operation on the Settings
  screen removes all of it.
- On first launch, AirControl shows a required notice: MediaPipe keeps
  camera/input data on-device but separately reports performance and
  API-utilization metrics to Google when a connection is available. AirControl
  starts only after explicit consent. To withdraw consent from an installed app,
  delete `%LOCALAPPDATA%\AirControl\.aircontrol-consent.json`; for a source
  build, delete `.aircontrol-consent.json` beside the active config file. The
  notice appears again and AirControl will not start until consent is given.
- Losing the hand releases a drag after a short grace period. A stalled camera or
  model triggers an independent watchdog that pauses control; keeping your hand
  away for a few seconds also pauses it.
- Default commands are navigation-only — there are no delete, close, send,
  purchase, or shell gestures.
- Windows blocks normal applications from injecting input into
  administrator/elevated windows and secure screens. AirControl does not request
  elevation or work around that protection.

## Build the installer yourself

Use Python 3.11 on Windows, then build the UI, the PyInstaller application
directory, and the Inno Setup installer:

```powershell
npm --prefix ui install
npm --prefix ui run build
py -3.11 -m pip install -e . pyinstaller
py -3.11 -c "from aircontrol.model import ensure_hand_model; ensure_hand_model('models/hand_landmarker.task')"
py -3.11 -m PyInstaller packaging/aircontrol.spec
& "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe" packaging\installer.iss
```

Keep `#define AppVersion` in `packaging/installer.iss` synchronized with
`project.version` in `pyproject.toml` whenever the release version changes.

## Development

After `setup.cmd`, from the project folder:

```powershell
.venv\Scripts\python.exe -m pytest
.venv\Scripts\python.exe -m aircontrol --practice
```

Recognition and desktop control are separated, so the whole gesture stack is
tested with synthetic landmark sequences — no camera required. The UI has its own
test suite (`npm --prefix ui run test`) and build (`npm --prefix ui run build`).

## Built with

AirControl was built by Rajdeep Mukherjee with AI pair-programming:

- **Claude (Anthropic)** — planning, orchestration, and code review.
- **Codex (OpenAI)** — implementation.

Both appear as `Co-Authored-By` tags on commits for provenance; neither is a
project contributor.
