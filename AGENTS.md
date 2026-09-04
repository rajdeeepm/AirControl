# AGENTS.md — guide for AI coding agents

This file orients an AI coding agent (Codex, Claude Code, or similar) working in
this repository. If a human pointed you here to "install this and explain what
it does," start with [What AirControl is](#what-aircontrol-is) and
[Set up and run](#set-up-and-run), then relay
[How to explain it to a user](#how-to-explain-it-to-a-user).

Human-facing docs: [README.md](README.md) (overview) and
[INSTRUCTIONS.md](INSTRUCTIONS.md) (complete usage manual). Prefer editing those
for end-user content; keep this file about *working in the codebase*.

## What AirControl is

AirControl turns an ordinary webcam into a touchless pointing device for Windows.
It reads the 21 hand-skeleton landmarks MediaPipe extracts **on-device**, and
maps hand gestures to mouse movement, clicks, scrolling, window management, and
user-defined custom gestures. It never uploads, streams, or stores camera video.

- **Backend:** Python daemon in `src/aircontrol/` (tracking, recognition,
  matching, OS input, SQLite storage, local WebSocket IPC).
- **Frontend:** React + Vite + TypeScript desktop UI in `ui/`, hosted in a
  `pywebview` window (not a browser tab).
- **Platform:** the live app runs on Windows and macOS (it injects OS input).
  The gesture logic and tests are platform-independent and run anywhere.
  `controller.native_input_sink()` picks the backend: `WindowsInputSink`
  (Win32 `SendInput`) or `MacInputSink` (Quartz). Both inherit their
  bookkeeping from `BaseInputSink`, so held-button and held-key safety is
  implemented once; a new platform implements only the `_emit_*` hooks.
  Keys crossing the sink boundary are always **Windows virtual-key codes** —
  see `mac_keymap.py` for how macOS translates them.

## Set up and run

`env/` and `.venv/` are git-ignored — create your own environment.

**Python (backend + tests)** — Python 3.11+:

```bash
python -m venv env
env/bin/python -m pip install -e ".[dev]"    # Windows: env\Scripts\python.exe
```

**UI (Node.js):**

```bash
npm --prefix ui install
```

**Hand model** — only needed to run *live* tracking (tests do not need it):

```bash
env/bin/python -c "from aircontrol.model import ensure_hand_model; ensure_hand_model('models/hand_landmarker.task')"
```

**Run without touching the OS** (safe anywhere): `python -m aircontrol --practice`.
Other entry points: `--app` (desktop UI), `--widget` (Airy only), `--calibrate`.
On Windows, the `*.cmd` launchers wrap all of this (see the README).

## Common commands

| Task | Command |
|---|---|
| Python tests | `env/bin/python -m pytest` |
| Python tests (sandboxed / read-only cache) | `env/bin/python -m pytest -p no:cacheprovider` |
| UI tests | `npm --prefix ui run test` |
| UI type-check + build | `npm --prefix ui run build` |
| Run recognition only (no OS input) | `python -m aircontrol --practice` |

Always run the Python suite and the UI build before proposing a change is done.
The whole gesture stack is tested with **synthetic landmark sequences — no
camera required**, so tests are fast and deterministic.

## Repository map

| Path | What lives there |
|---|---|
| `src/aircontrol/` | The daemon and all gesture logic |
| `src/aircontrol/recognizer.py` | Built-in static pose classifier (open palm, fist, point, pinch, scroll, swipe) |
| `src/aircontrol/segmentation.py` | Motion onset/offset segmenter |
| `src/aircontrol/matcher.py`, `dtw.py`, `trajectory.py` | Custom-gesture matching (DTW over normalized landmark + engineered features) |
| `src/aircontrol/pipeline.py` | Frame processing: single- and two-hand modes, custom gesture/pose recognition |
| `src/aircontrol/recording.py` | Custom gesture capture (explicit press-to-start/stop, motion trim, pose hold) |
| `src/aircontrol/store.py` | SQLite store (gestures, exemplars, mappings, profiles, settings) with schema migrations |
| `src/aircontrol/daemon.py`, `ipc.py`, `app.py` | Command handling, JSON IPC over `127.0.0.1`, camera loop |
| `src/aircontrol/controller.py`, `input_sink.py` | OS input dispatch and the shared sink base |
| `src/aircontrol/mac_input_sink.py`, `mac_keymap.py` | macOS input dispatch (Quartz) and key translation |
| `src/aircontrol/calibration.py`, `profile.py`, `settings.py`, `gate.py` | Personalization and confidence gating |
| `ui/` | React desktop UI; screens under `ui/src/screens/` |
| `tests/` | Pytest suite (synthetic, no camera) |
| `packaging/` | PyInstaller spec + Inno Setup installer |

## Hard constraints — do not break these

1. **On-device only.** Never add code that uploads, streams, or persists camera
   frames or landmark data off the machine. The privacy guarantee is core.
2. **Recognition and OS input stay separated.** This is what lets tests run on
   synthetic landmarks with no camera. Keep new logic testable the same way.
3. **Motion vs. pose custom gestures never cross-fire**, and a custom gesture
   fires only at a high similarity floor (see `MIN_CUSTOM_CONFIDENCE` in
   `pipeline.py`). Preserve these when touching matching.
4. **Store changes need a migration.** Bump `_SCHEMA_VERSION` and add a
   migration block in `store.py`; keep old databases loadable.
5. **Default actions are navigation-only.** Do not add destructive or dangerous
   gestures/hotkeys by default.

## Conventions

- Match the surrounding code style; keep recognition changes backed by tests.
- Do not commit or push unless the human asks. Branch off the default branch for
  new work.
- When you change end-user behavior, update `README.md` and/or
  `INSTRUCTIONS.md` in the same change.

## How to explain it to a user

> AirControl lets you control your Windows PC with hand gestures over your
> webcam — move the mouse by pointing, pinch to click and drag, scroll with two
> fingers, and manage windows with three-finger swipes. It starts disarmed and
> only responds after you hold an open palm to arm it. You can also record your
> own gestures — either a motion or a held hand pose — and map each to an action
> or keyboard shortcut. All hand tracking runs on your own computer; camera
> video never leaves the machine.

For step-by-step usage, point them to [INSTRUCTIONS.md](INSTRUCTIONS.md).
