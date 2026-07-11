# AirControl — Spec 2: Calibration & Reliability Core

**Date:** 2026-07-11
**Status:** Approved for planning (open questions resolved from the build-direction doc §4–§6, §11, §13)
**Owner:** Rajdeep Mukherjee (planning/orchestration: Claude; implementation: Codex)

---

## 0. Context

Foundation (Spec 1, complete: 119 tests green) delivered the substrate:
trajectory representation + normalization (`trajectory.py`), rolling buffer
(`buffer.py`), SQLite store with a `calibration_profiles` table (`store.py`),
metrics harness (`metrics.py`), the T1/T2/T3 confidence-gate seam (`gate.py`),
loopback IPC (`ipc.py`), and the daemon/pipeline assembly. This spec fills those
seams with the reliability mechanics from the build direction (§4 calibration,
§5 clutch, §6 segmentation, §11 undo, §13 metrics targets).

Phase-1 exit criterion from the build direction: **a full workday of the
owner's use with < 1 false fire.**

## 1. Goal & Non-Goals

### Goal
1. **Guarded dispatch fix (carried over from Spec 1, blocking, first task):**
   `Pipeline._gated_action_events` currently routes engine-generated releases
   (`LEFT_UP`) through the confidence gate. Classify actions into
   `RELEASING` (always dispatch: `LEFT_UP`) vs `INITIATING` (gated:
   everything else) so an abstain can never strand held input.
2. **Calibration flow, HUD-guided** (camera preview visible throughout, clear
   recording indicator; re-skinned as the wizard UI in Spec 5):
   - Step 1 *Framing*: capture typical hand distance + define the interaction
     volume (normalized bounding box in camera space).
   - Step 2 *Hand snapshot*: open palm, fist, slow rotation → user hand-size
     constant (wrist→middle-MCP) for normalization.
   - Step 3 *Motion signature*: one slow + one fast deliberate swipe → personal
     velocity floor/ceiling for "deliberate motion."
   - Step 4 *Negative-class capture* (the most important step): 30 s of normal
     desk work → incidental-motion trajectory set; fit density model (see §
     Goal 5) used for T3 rejection.
   - Step 5 *Lighting check*: landmark-jitter + brightness statistics; store a
     condition profile; concrete advice on failure.
   - Profile persists via `store.calibration` (payload JSON schema defined in
     the plan); recalibration entry point re-runs any subset of steps.
3. **Clutch strategies** (config-selectable, default `wake_pose`):
   - `wake_pose` (default): open palm facing camera, stable 400 ms inside the
     interaction volume arms for a 5 s window that resets on each recognized
     gesture; closing-fist hold or timeout disarms. (The existing engine's
     open-palm/fist holds are refactored into this strategy.)
   - `spatial_zone`: gestures count only above a calibrated height plane.
   - `always_on`: expert mode, config-only, clearly labeled; requires elevated
     gate thresholds to be set.
4. **Temporal segmentation state machine** (§6): IDLE → ARMED → MOTION_ONSET
   (velocity above calibrated floor N consecutive frames) → IN_GESTURE →
   MOTION_OFFSET (below floor M frames or 2.5 s max) → CANDIDATE
   (trajectory slice handed onward) → ARMED. Hysteresis on thresholds;
   hand-entry/exit debouncing (presence must be stable before onset counts);
   candidates measured via `metrics.note_candidate` even though actions still
   come from the heuristic engine this spec.
5. **Incidental-motion density model** for T3: embed calibration-step-4
   trajectories as normalized feature vectors (this spec: summary statistics
   over `NormalizedTrajectory` — velocity histogram + amplitude + duration;
   Spec 3 swaps in encoder embeddings behind the same interface), fit a kNN
   density; `distance(candidate) → T3 input` wired into `ConfidenceGate`.
6. **Undo window** (§11): for 3 s after any fired action, `Esc` (keyboard v1;
   dedicated gesture later) reverses reversible actions and logs
   `metrics.note_false_positive()`. Reversibility map:
   - `SWITCH_NEXT` ↔ `SWITCH_PREVIOUS` (dispatch the inverse)
   - `TASK_VIEW` → send Esc (closes task view)
   - `SHOW_DESKTOP` → send Win+D again (toggle)
   - `SCROLL(n)` → `SCROLL(-n)`
   - Pointer moves / pinch click-drag: not reversible → not in the map; undo
     window simply logs the FP.
7. **Metrics closure & drift**: FP/hour (armed + unclutched), candidates/hour,
   segmentation-quality counters surfaced in the periodic `metrics` IPC event;
   landmark-jitter drift detector that sets a `recalibration_recommended` flag
   in status events.

### Non-Goals
- Embedding model / DTW matcher (Spec 3) — segmentation + density operate on
  trajectory statistics this spec.
- Custom gesture recording / confusability (Spec 4).
- GUI wizard (Spec 5; this spec's calibration is HUD-guided steps).
- Changing the gesture vocabulary or adding new dispatch actions beyond the
  undo inverses.
- Gaze gating (v2 parking lot).

## 2. Resolved Decisions (from build direction)
1. Calibration UX v1 = HUD-guided steps in the existing OpenCV preview window
   (§4: "not a script"), driven by keyboard advance (Space/Enter) + on-screen
   instructions; every step shows the camera preview + recording indicator.
2. Default clutch = wake pose (§5 explicitly).
3. Undo scope = the reversibility map in Goal 6; unlisted actions log-only.
4. `always_on` ships config-only behind `clutch.mode = "always_on"` with a
   required `acknowledged_expert_mode: true` config flag (§5 "clearly
   labeled"); refuses to start without the acknowledgement flag.

## 3. Components
- `calibration.py` — `CalibrationProfile` dataclass + step-runner state machine
  (pure logic; HUD text/preview supplied by app layer via callbacks);
  serialization to/from `calibration_profiles.payload` JSON.
- `clutch.py` — `ClutchStrategy` protocol (`update(sample, now) -> ClutchState`)
  + `WakePoseClutch`, `SpatialZoneClutch`, `AlwaysOnClutch`; engine consumes
  clutch state instead of its inline open-palm/fist logic.
- `segmentation.py` — `SegmentationMachine(profile, config)` consuming
  per-frame velocity from the rolling buffer; emits `CandidateSegment`.
- `density.py` — `IncidentalDensity.fit(trajectories)` / `.distance(traj)`;
  persisted inside the calibration payload.
- `undo.py` — `UndoManager(window_seconds=3.0)` with the reversibility map,
  `note_fire(action, ts)`, `try_undo(now) -> Action | None`, FP logging hook.
- Pipeline wiring: clutch → engine arming; segmentation in parallel on the
  buffer (candidates → metrics now, matcher in Spec 3); gate thresholds and
  T3 density from the active profile; undo manager wrapping dispatch; Esc key
  handled in `app.py`.

## 4. Testing Strategy
All pure-logic (WSL pytest): calibration step machine with synthetic
observation scripts; clutch strategies (arm/disarm timing, volume checks,
expert-flag refusal); segmentation truth tables (onset/offset hysteresis,
entry/exit debounce, max-duration); density fit/distance sanity (incidental
close, deliberate far); undo map inverses + window expiry + FP logging;
integration: scripted day-at-desk sequence must produce zero fired actions
while unclutched. User validates live behavior on Windows per the established
split, including a first real stress test (§9-style: 60 s normal work, count
false fires).

## 5. Definition of Done
- All new modules unit-tested; full suite green.
- `python -m aircontrol --calibrate` runs the HUD-guided flow end-to-end on
  Windows (user-verified) and persists an active profile.
- Segmentation candidates/hour and FP/hour visible in metrics export.
- Undo reverses the four mapped actions live (user-verified).
- LEFT_UP bypass fix in place with a regression test.
