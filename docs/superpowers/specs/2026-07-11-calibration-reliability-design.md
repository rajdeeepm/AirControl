# AirControl — Spec 2: Calibration & Reliability Core (DRAFT)

**Date:** 2026-07-11
**Status:** Draft — pending Foundation (Spec 1) completion and user review
**Owner:** Rajdeep Mukherjee (planning/orchestration: Claude; implementation: Codex)

---

## 0. Context

Foundation (Spec 1) delivered the substrate: trajectory representation +
normalization (`trajectory.py`), rolling buffer (`buffer.py`), SQLite store
with a `calibration_profiles` table (`store.py`), metrics harness
(`metrics.py`), the T1/T2/T3 confidence-gate seam (`gate.py`), loopback IPC
(`ipc.py`), and the daemon/pipeline assembly. This spec fills those seams with
the reliability mechanics from the build direction (§4 calibration, §5 clutch,
§6 segmentation, §11 undo, §13 metrics targets).

Phase-1 exit criterion from the build direction: **a full workday of the
owner's use with < 1 false fire.**

## 1. Goal & Non-Goals

### Goal
1. **Calibration flow** (terminal/HUD-guided v1; the polished wizard UI comes in
   Spec 5): framing + interaction volume, hand snapshot (hand-size constant),
   motion signature (personal velocity floor/ceiling), **negative-class capture**
   (30 s of normal desk work → incidental-motion distribution), lighting check.
   Results persist to `calibration_profiles` and parameterize normalization,
   segmentation, and the gate.
2. **Clutch mechanisms** as strategy objects: wake-pose (default: open palm
   400 ms inside interaction volume, arm window ~5 s reset on each recognized
   gesture, closing-fist disarm), spatial-zone (height plane), always-listening
   (expert, high thresholds). Config-selectable.
3. **Temporal segmentation state machine** per §6: IDLE → ARMED → MOTION_ONSET
   (velocity above calibrated floor N frames) → IN_GESTURE (buffering) →
   MOTION_OFFSET (below floor M frames or max 2.5 s) → CANDIDATE, with
   hysteresis and hand-entry/exit debouncing. Candidates feed the (future)
   matcher; this spec measures them (candidates/hour) even while the heuristic
   recognizer still drives actions.
4. **Real confidence gating:** incidental-motion density model (Gaussian or kNN
   in normalized-trajectory feature space over the negative-class capture) wired
   to T3; conservative default T1/T2 for the heuristic path.
5. **Undo window** (§11): for 3 s after any fire, Esc (v1; dedicated gesture
   later) reverses reversible actions and logs a false positive to `metrics.py`.
6. **Metrics closure:** FP/hour (unclutched + armed), segmentation quality
   counters, latency, all exported locally; recalibration prompt on landmark
   jitter drift.

### Non-Goals
- Embedding model / DTW matcher (Spec 3). The segmentation + gate operate on
  simple trajectory statistics this spec.
- Custom gesture recording / confusability (Spec 4).
- GUI wizard (Spec 5 re-skins the calibration flow; data model is this spec).
- Changing the existing gesture vocabulary or dispatch set.

## 1.5 Carried-over risk from Spec 1
`Pipeline._gated_action_events` routes engine-generated releases (e.g.
`LEFT_UP` ending a pinch drag) through the confidence gate. Harmless today
(permissive defaults always fire), but once real thresholds land, an abstain
mid-drag could strand a held mouse button. Spec 2 MUST classify actions so
that state-releasing actions (`LEFT_UP`) bypass the gate the same way
`_forced_action_events` already does for safety/manual paths.

## 2. Open Questions (to resolve at brainstorm before planning)
1. Calibration UX in v1: OpenCV-HUD-guided steps vs terminal prompts + HUD?
   (Recommend HUD-guided: camera preview visible, matches §4 "not a script".)
2. Default clutch for the owner's daily-driver test: wake pose (recommended) or
   spatial zone?
3. Undo scope v1: which actions are reversible (scroll: counter-scroll? app
   switch: alt-tab back? task view: esc?) — enumerate exactly.
4. Does always-listening mode ship at all in v1, or config-only behind an
   advanced flag? (Build direction says offer it labeled for experts.)

## 3. Sketch of Components
- `calibration.py` — step runner + `CalibrationProfile` dataclass (hand_size,
  interaction_volume, velocity_floor/ceiling, incidental_stats, lighting);
  persists via `store.calibration`.
- `clutch.py` — `ClutchStrategy` protocol + `WakePoseClutch`,
  `SpatialZoneClutch`, `AlwaysOnClutch`.
- `segmentation.py` — the state machine, parameterized by the profile; emits
  `CandidateSegment(trajectory, t_onset, t_offset)`.
- `density.py` — incidental-motion density estimate + distance scoring for T3.
- `undo.py` — reversibility registry + 3 s window + FP logging.
- Pipeline wiring: clutch gates engine arming; segmentation runs in parallel on
  the rolling buffer; gate thresholds come from the active profile.

*(Detailed interfaces, schema payloads, and tests to be pinned in the Spec 2
plan after Foundation merges and the open questions are answered.)*
