# AirControl — Spec 6: Desktop App, In-App Preview, Status Widget

**Date:** 2026-07-14
**Status:** Approved for planning (user-driven from live use of the v1 UI)

## 0. Why

Live use of the Spec-5 UI surfaced three gaps between "a daemon with a web page"
and "an app you downloaded":

1. The UI is a **browser tab** pointed at a localhost URL. It should be a real
   application window.
2. The **camera preview is a separate OpenCV window** floating next to the app.
   It should be inside the app.
3. When the app is not focused, the user **cannot tell whether the system is
   armed**. A taskbar entry is not enough — gesture control is ambient, so its
   state indicator must be ambient too.

## 1. Decisions

**Desktop shell = `pywebview`, not Tauri.** Tauri (the Spec-5 deferral) needs a
Rust toolchain on Windows and cross-building from the WSL dev environment is
impractical. `pywebview` hosts the built React UI in a native WebView2 window,
installs with pip, keeps the app a single Python process (the daemon is already
Python), and packages to a real `.exe` with PyInstaller later. Tauri stays
available as a future swap: the UI is a plain static bundle talking to a
loopback socket, so the shell is replaceable.

**Preview transport = binary JPEG over the existing loopback WebSocket.** No new
server, no new port. Frames are sent only while the UI asks for them
(`set_preview {enabled}`), default off. **The privacy invariant is unchanged and
must stay literally true: frames live in RAM, travel only to 127.0.0.1, are
never written to disk and never leave the machine.** Only the annotated preview
(the same skeleton + HUD overlay the OpenCV window drew) is encoded.

**`--serve` runs headless.** With the preview inside the UI, the daemon must not
create an OpenCV window at all. The run loop gains a headless path (no
`namedWindow`/`imshow`/`waitKey`); the UI owns arm, undo, and quit.

**Widget = its own process, Tkinter (stdlib).** Frameless, always-on-top,
draggable, position persisted. It connects to the same loopback socket and
renders one thing honestly: armed / idle / daemon-down. Click toggles arm.
Keeping it a separate process means a widget crash can never take down gesture
control, and it can run without the main window open.

## 2. Scope

### In
- `ipc.py`: `set_preview` command; binary broadcast channel; preview frames as
  binary WS messages (JSON envelope unchanged for all other events).
- `app.py`: headless run path when `config.ipc.enabled`; preview encoder
  (reuses `GestureOverlay` so the UI shows the same annotated view), capped at
  ~15 fps and downscaled, only while preview is enabled.
- `webview_app.py`: `--app` mode — starts the daemon headless, serves `ui/dist`
  on a loopback ephemeral port, opens a `pywebview` native window on it.
- `widget.py`: `--widget` mode — Tkinter always-on-top status badge, WS client,
  click-to-arm, drag-to-move, position persisted in the store/config dir.
- `app.cmd`: launches the native app **in live mode** (arming still requires a
  deliberate open-palm hold, so this is safe) plus the widget.
- UI: preview surface on Status (the hero), preview on/off control, LIVE vs
  PRACTICE badge, and a design pass once the preview reshapes the layout.
- `pywebview` added to project dependencies; launcher dependency probes updated.

### Out
- Tauri packaging; PyInstaller `.exe` bundling (natural next step, not this spec).
- Streaming raw (un-annotated) frames anywhere.
- Moving the calibrate/record/arena HUD flows into the UI (they remain OpenCV
  guided flows; the app links to them).

## 3. Definition of Done
- `app.cmd` opens ONE native window. No browser. No stray OpenCV window.
- The camera preview, with the skeleton overlay, renders inside that window.
- A small always-on-top badge shows armed/idle/down at all times, survives the
  main window being closed, and toggles arming on click.
- Preview defaults off, streams only on request, and no code path writes a frame
  to disk (test-enforced).
- Full python suite + vitest green.
