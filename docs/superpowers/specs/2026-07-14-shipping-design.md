# AirControl — Spec 7: Shippable Product (UI redesign, Airy, .exe)

**Date:** 2026-07-14
**Status:** Approved for planning (user-supplied mockups + distribution requirement)

## 0. The distribution decision (drives everything)

**Keep Python. Package it invisibly.** The product's value is the recognition
pipeline (MediaPipe, DTW matcher, calibration, abstention gate, 384 tests).
Rewriting in Rust/JS to obtain an `.exe` would discard the product to gain a
file format.

Toolchain:
- **PyInstaller** (`--windowed` / `--noconsole`) → one `AirControl.exe`. Python,
  MediaPipe, OpenCV and the hand model are bundled inside. No console window ever
  appears.
- **Inno Setup** → `AirControl-Setup.exe`: Start-menu entry, desktop shortcut,
  uninstaller, optional run-at-login.
- **GitHub Actions** (`windows-latest`) → builds both on tag push and attaches
  them to a **GitHub Release**.
- User experience: download `AirControl-Setup.exe` → run → app appears like any
  other app. No terminal, no scripts, no Python.

Tauri stays rejected: it cannot host the Python ML pipeline, and a Rust rewrite
is a different product.

## 1. UI redesign (from the user's mockups)

Navigation: **Dashboard · Gestures · Settings · Calibration · Appearance · About**.

- **Dashboard**: camera preview as the hero (live skeleton overlay, LIVE badge,
  tracking-quality line), a prominent **ARMED toggle switch** (not a button),
  sensitivity + smoothing sliders with live numeric readouts, a gesture-mapping
  summary list, a Calibrate call-to-action, and a numbered **Quick Start** strip
  for first-run users.
- **Gestures**: mapping cards in a grid — each with an icon, the gesture name,
  the action it performs, and an enable/disable toggle. "Add Gesture" launches
  recording.
- **Settings**: sensitivity, smoothing, cursor speed, dominant hand, clutch mode,
  privacy controls (delete everything).
- **Calibration**: status of the active profile and a recalibrate action.
- **Appearance**: **light / dark / system theme** toggle (persisted).
- **About**: version, the user guide, privacy statement.

**Light mode is a first-class requirement**, not an inversion of dark. Both
themes are authored: tokens are defined per-theme and every surface is checked
for contrast in both.

## 2. Airy — the companion widget

A frameless, always-on-top **WebView window** (second `pywebview` window, not
Tkinter) rendering an animated character in HTML/CSS/SVG:
- **Active**: bright cyan eyes, glowing halo, gentle idle float/wave. Label
  "Active — tracking your gestures".
- **Inactive**: closed/dim eyes, no halo, muted. Label "Inactive — gesture
  tracking paused".
- **Offline**: hollow, greyed. Label "Offline — AirControl is not running".
- Click toggles arm/pause; drag repositions; position persists; a small close
  button hides it. State is **never color-only** — the label always names it.

## 3. Live settings (the sliders must actually do something)

New persisted app settings, editable from the UI at runtime and applied to the
running pipeline without a restart:

| Setting | Range | Applies to |
|---|---|---|
| `sensitivity` | 0–100 (default 50) | gate `t1_top1` = `0.95 − sensitivity/100 × 0.35` (higher = fires more readily) |
| `smoothing` | 0–100 (default 64) | `pointer_smoothing` alpha = `max(0.05, 1 − smoothing/100)` |
| `cursor_speed` | 0.25–3.0× (default 1.0) | `pointer_pixels_per_palm` multiplier |
| `dominant_hand` | left/right (default right) | preferred tracking hand |
| `theme` | light/dark/system (default system) | UI only |
| `airy_enabled` | bool (default true) | show/hide the companion |

Persisted in SQLite (survives restart), exposed over IPC (`get_app_settings` /
`set_app_setting`), and applied live.

## 4. Definition of Done
- One installer `.exe`; installing and launching shows the app with no console.
- Light and dark both ship and both pass contrast checks.
- Sliders visibly change behavior immediately.
- Airy floats above everything and honestly reports Active/Inactive/Offline.
- GitHub Actions produces the release artifacts on a tag.
