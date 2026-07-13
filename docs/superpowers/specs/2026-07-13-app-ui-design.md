# AirControl — Spec 5: App UI & Mapping Editor

**Date:** 2026-07-13
**Status:** Approved for planning (decisions resolved from build direction §2, §4, §10–§12, §14 Phase 4)
**Owner:** Rajdeep Mukherjee (planning/orchestration: Claude; implementation: Codex + Claude/impeccable for UI design quality)

## 0. Context
Specs 1–4 delivered the daemon: calibrated reliability core, DTW matcher,
custom-gesture recording, and the practice arena — all HUD/CLI-driven, 289
tests green. Spec 5 gives AirControl its product face: the app UI from build
direction §14 Phase 4 (gesture library with skeleton animations, mapping
editor with verbs-by-context and shortcut recorder, settings, privacy
controls, starter onboarding).

## 1. Decisions
1. **Frontend = React + TypeScript + Vite in `ui/`**, talking v1 JSON over the
   existing loopback WebSocket (`127.0.0.1:8787`). **Tauri packaging is a
   deferred Windows-side step** (Rust toolchain stays off this machine's WSL);
   until then the UI runs as a local web app opened in the browser by a
   launcher. This honors "do not let the shell choice block the daemon work."
2. **Skeleton animations come from stored landmark trajectories** (store →
   IPC → canvas), never video — the privacy story stays literally true and no
   live frame streaming is needed for v1 UI.
3. **Camera-guided flows (calibrate/record/arena) stay OpenCV-HUD** in v1; the
   UI links/launches them and reflects their results. Migrating them into the
   UI (landmark streaming) is the v1.5 headline, not this spec.
4. **Shortcut recorder requires a new `hotkey` mapping action**
   (`{"kind": "hotkey", "keys": [<vk>, ...]}`) dispatched via the existing
   `InputSink.hotkey`; flagged non-reversible (undo logs FP only).
5. UI is **read-mostly over live state, write over library/mappings**:
   settings screen displays effective config + privacy controls
   (delete-everything, kill switch = pause command); editing config.json from
   the UI is deferred.

## 2. Scope
### In
- **IPC v2 additions (backward-compatible, still `v:1` envelope):** commands
  `list_library` (gestures + mapping + stats + one downsampled exemplar
  trajectory each for animation), `set_mapping`, `delete_gesture`,
  `rename_gesture`, `get_metrics`, `get_settings`, `delete_everything`;
  responses as new event types `library`, `metrics_snapshot`, `settings`,
  `ack`. Commands carry an optional `id` echoed in the response for
  request/response correlation.
- **`hotkey` action kind** end-to-end (store JSON → pipeline `_mapped_action`
  → controller → sink; blocked-by-default list for dangerous combos, e.g.
  Alt+F4, behind an `allow_risky_hotkeys` config flag).
- **UI screens** (designed with the impeccable skill):
  1. **Status** — armed indicator, live event feed (actions with confidence,
     abstains with reason), metrics tiles (FP/hr, candidates/hr, latency),
     pause/arm toggle, kill-switch.
  2. **Library** — cards per custom gesture with looping skeleton animation
     (canvas, from trajectory), health stats (confirms/rejects/threshold
     offset), rename/delete; built-in gesture vocabulary shown as a fixed
     reference section.
  3. **Mapping editor** — curated verbs by context (Media, Calls, Windows,
     Browser, Presentation, Reading, System per §10) + **shortcut recorder**
     field capturing arbitrary key combos as `hotkey` actions; per-gesture
     assignment; destructive/risky actions visibly flagged.
  4. **Settings & Privacy** — effective clutch/gate/camera settings
     (read-only v1), privacy panel: data location, what is stored (landmarks
     only), delete-everything button (double-confirm), links to calibrate/
     record/arena launchers.
- **Launcher**: `app.cmd` starts the daemon with IPC enabled (practice-safe
  default: current `start.cmd` behavior unchanged) and opens the UI;
  `ui/` ships a static production build served by the daemon? No — keep the
  daemon network-free beyond the WS: launcher opens `ui/dist/index.html`
  directly (file:// won't allow WS to localhost in some browsers → serve via
  a tiny `python -m http.server 127.0.0.1`-bound static server from the
  launcher, NOT from the daemon).
- Starter onboarding: first UI run shows the built-in vocabulary + a
  "record your first gesture" call-to-action (starter pack of ≤5 built-ins is
  inherent — the heuristic vocabulary IS the starter pack; modeled restraint).

### Out (deferred)
- Tauri/desktop packaging (Windows step with the user).
- In-UI camera flows / live skeleton streaming.
- Config editing from the UI; per-app profiles UI (v2 per §10).
- Cloud/community anything.

## 3. Architecture
```
ui/ (Vite + React + TS)
  src/lib/ws.ts        — typed v1 client: connect, request(id) ↔ ack/reply, event bus
  src/lib/types.ts     — mirrors ipc.py schema (hand-written, versioned)
  src/lib/skeleton.ts  — trajectory → canvas renderer (21-point hand, bones)
  src/screens/{Status,Library,Mappings,Settings}.tsx
  src/App.tsx          — shell, nav, connection state
daemon (python)
  ipc.py               — new event builders + command names
  daemon.py            — command handlers hitting Store/Metrics/Pipeline
  pipeline.py/controller.py — hotkey action kind
```
UI tests: vitest for ws client + skeleton math; Playwright (webapp-testing)
smoke against a fake daemon (FakeTransport-equivalent WS server in a pytest
fixture or a tiny node mock).

## 4. Definition of Done
- New IPC commands unit-tested (python) incl. correlation ids and
  delete_everything actually wiping.
- `hotkey` mapping dispatches through a DryRun sink in tests; risky combos
  blocked unless flagged.
- `npm run build` produces `ui/dist`; vitest green; Playwright smoke green
  against mock daemon in WSL.
- User on Windows: `app.cmd` opens the UI, sees live status while
  `practice`-mode daemon runs, edits a mapping, watches a custom gesture fire
  with its confidence in the feed.
