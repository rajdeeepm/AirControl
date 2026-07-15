# AirControl — Spec 8: Airy as the Real-Time Feedback Layer

**Date:** 2026-07-15
**Status:** Approved for planning (user direction + reference art)

## 0. Thesis
Air gestures give no physical click sensation. Airy replaces that missing
feedback with immediate visual confirmation, becoming **the visual equivalent
of the physical feedback a mouse provides**. It reacts at three stages, not just
after an action fires:
1. **Detection** — "I can see your hand." (from `status.hand_visible`)
2. **Recognition** — "I understand the gesture." (from candidate events,
   `active_pose`, `hold_progress`)
3. **Execution** — "The action happened." (from action events + confidence)

The honesty rule from PRODUCT.md is absolute: **Airy only reacts to real
events.** It never animates an outcome the daemon did not actually report, and
the confidence it shows is the matcher's true top-1 score.

## 1. Reaction model (driven by existing + enriched WS events)

Continuous (subtle only): cursor pose → eyes track travel direction, faint
glow; nothing large per cursor move. Discrete (150–350 ms): click wink/pulse,
scroll float, swipe dash, mute cover-mouth, volume rings, AI listening pose.
Errors/state changes (larger, clearer): camera error red state, rejection
shake, sleep.

| Signal (source) | Airy response |
|---|---|
| hand becomes visible (`status.hand_visible`) | wakes, looks toward hand, eyes brighten |
| armed + cursor pose (`active_pose`=Pointer) | tracking halo, pupils follow direction |
| hold in progress (`hold_progress`) | ring fills around Airy (acts as the hold timer) |
| candidate fire, high conf | crisp cyan/green confirmation pulse |
| candidate abstain / low conf (`reason`) | ring flickers amber; tiny shake, no action |
| action `left_down`→`left_up` quick | wink / single pulse (click) |
| action drag (left_down held + move) | leans/grabs; settles on release |
| action `scroll` | floats up/down briefly |
| action `switch_next`/`switch_previous`/`task_view`/`show_desktop` | dashes left/right with a motion trail |
| action category `volume` | sound-wave rings |
| action category `mute` | covers mouth |
| action category `ai_activate` | brighter, listening pose |
| generic `hotkey`/`escape`/`system` | short neutral pulse + label |
| armed→false | eyes close, goes to sleep |
| camera error | clear red warning state (not playful) |
| camera lost hand while armed | scans around briefly, returns to idle |

## 2. Backend enablers (small)
1. **Action semantic category on action events.** Add a `category` field to
   `action_event` derived from the action kind AND, for `hotkey` actions, the
   mapping's curated-verb identity (the verb catalog already knows Mute / Volume
   up / Volume down / AI activation). Categories:
   `click | drag | scroll | window | volume | mute | ai_activate | system |
   hotkey | pointer`. Airy's reaction table keys off `category`, so new mapped
   gestures light up correctly. If a hotkey has no known verb, category =
   `hotkey` (neutral) — never guess.
2. **Airy settings** (app_settings, live + persisted):
   `airy_feedback_level` = `full | subtle | minimal | hidden`;
   `airy_sounds` (bool, default false); `airy_animation_intensity`
   (0–100); `airy_size` (small/medium/large); `airy_on_top` (bool);
   plus the existing `airy_enabled`. Reduced-motion is honored from the OS and
   also forced when level=minimal.
3. **Menu commands.** `focus_dashboard` (raise/focus the main window),
   reuse `pause`/`toggle_arm`, `quit`. Recalibrate and switch-profile are
   **not** launchable from the running app in v1 (calibration is a separate
   guided flow) — the menu routes those to the dashboard with a clear note
   rather than faking them.

## 3. Airy front-end (ui/airy/index.html — the bulk of the work)
A self-contained, offline CSS/SVG state machine consuming the WS events:
- Three-stage visual states layered (detection glow → recognition ring →
  execution animation), returning to idle/sleep per armed state.
- Feedback intensity honored: `full` = expressions + movement (+ optional
  sound cues if `airy_sounds`); `subtle` = glow + small confirmations;
  `minimal` = status dot + a single action pulse, reduced-motion; `hidden` =
  the window is not shown (tray/dashboard still control state).
- Confidence signaling: stable cyan glow (high), progress ring (forming),
  amber flicker (low), tiny shake (rejected), green pulse (completed).
- **Click Airy → compact control menu**: Pause/Resume, Open dashboard, Camera
  status, Exit (and the deferred items routed to the dashboard). Drag still
  moves; the menu never blocks the desktop.
- **Transient action label** ("LEFT CLICK", "SCROLLING", "WINDOW SWITCH",
  "LISTENING") that appears briefly on execution and auto-dismisses; never
  obstructs work.
- Poses match the reference art: raised wave when active/greeting, lowered arm
  when inactive, sleeping (closed eyes) when paused, distinct purple pulse for
  the right-click/secondary category, drag lean with motion trail, scroll
  float, window-swipe dash.

## 4. Definition of Done
- Airy reacts at all three stages from real events; no fabricated reactions.
- Feedback level + granular controls persist and change behavior live.
- Clicking Airy opens the control menu; the transient action label works.
- Reduced-motion respected; `hidden` truly hides.
- Fully offline (no external URLs); python + vitest green.
