# AirControl DTW Matcher (Spec 3) Implementation Plan

> **For agentic workers:** Executed by **Codex** (GPT-5.6) task-by-task, orchestrated/reviewed/committed by Claude. Checkbox steps; each task independently testable.

**Goal:** DTW nearest-neighbor gesture matching behind a `TrajectoryMatcher` seam, wired so gate-approved matches dispatch mapped actions — custom gestures become functional without any trained model.

**Architecture:** `dtw.py` (pure distance) → `matcher.py` (protocol + DtwMatcher over store exemplars) → pipeline hookup (segment → matcher → gate → mapped dispatch). Additive: empty library = today's behavior exactly.

**Tech Stack:** Python 3.13 (`env/` venv), numpy only. Test cmd: `env/bin/python -m pytest -p no:cacheprovider`. All 232 existing tests stay green. Codex must not git-commit.

## Global Constraints
- Same style/env/privacy rules as prior plans (frozen slotted dataclasses, `from __future__ import annotations`, loopback-only, no frames persisted, nothing outside the project dir).
- Matcher inference budget: DTW pair under 10 ms at T=45 with band=8 (assert in a benchmark test, generous 50 ms CI bound to avoid flakes).

---

### Task 1: `dtw.py`
**Files:** Create `src/aircontrol/dtw.py`; Test `tests/test_dtw.py`.
**Interfaces:** `dtw_distance(a: np.ndarray, b: np.ndarray, *, band: int = 8, velocity_weight: float = 0.3) -> float` where a/b are `(T, 21, 3)` float32 canonical tensors (from `NormalizedTrajectory`); per-frame cost = mean landmark Euclidean distance + `velocity_weight` × mean velocity-difference norm (velocities derived internally by finite difference); Sakoe–Chiba band on the T×T grid; returns path-length-normalized cost (total/steps). `trajectory_dtw(a: NormalizedTrajectory, b: NormalizedTrajectory, *, band=8, velocity_weight=0.3) -> float` convenience.
**Tests:** identity → 0; symmetry; a time-warped copy (resampled speed profile) scores well below a genuinely different motion (ratio ≥ 2×); band respected (band=1 vs band=44 differ for warped pairs, same for identical); benchmark marker: single call < 50 ms.
**Steps:** failing tests → verify red → implement → green → full suite.

### Task 2: `matcher.py`
**Files:** Create `src/aircontrol/matcher.py`; Test `tests/test_matcher.py`.
**Interfaces:** `@dataclass MatchResult(gesture_id: int | None, top1: float, top2: float, scores: dict[int, float])`; `TrajectoryMatcher` protocol with `match(traj: Trajectory) -> MatchResult` and `refresh() -> None`; `DtwMatcher(store: Store, *, resample_length: int = 45, band: int = 8, velocity_weight: float = 0.3, max_exemplars: int = 15)`: `refresh()` loads all gestures + exemplars from the store (normalize once, cache tensors, cap per-gesture at max_exemplars most recent); `match()` normalizes input, per-gesture score = max over exemplars of `1/(1+dtw)`, `top1` = best gesture score, `top2` = best OTHER gesture score (0.0 when <2 gestures), `gesture_id` = argmax (None when library empty → `MatchResult(None, 0.0, 0.0, {})`).
**Tests:** empty library → None/0/0; single-gesture library → top2 == 0; seeded store with 3 synthetic gesture classes (distinct motion generators, 8 exemplars each with jitter) → held-out samples of each class match correctly with top1 > top2 (margin > 0.05); refresh picks up newly added gestures; scores dict has all gesture ids.
**Steps:** red → implement → green → full suite.

### Task 3: Pipeline hookup + few-shot bench
**Files:** Modify `src/aircontrol/pipeline.py`, `src/aircontrol/daemon.py`; Test `tests/test_matcher_integration.py`.
**Interfaces:** `Pipeline.__init__` builds `DtwMatcher(store)` + `refresh()` when BOTH profile is not None AND store is not None; keeps `self.matcher: TrajectoryMatcher | None`. In `process()`, on a `CandidateSegment`: when matcher present and its `refresh`ed library non-empty, `result = matcher.match(segment.trajectory)`; gate decision now uses `gate.evaluate(top1=result.top1, top2=result.top2, incidental_distance=...)`; when decision fires AND `result.gesture_id` maps to an enabled mapping (store.mappings.for_gesture(gesture_id)) whose action JSON has `{"kind": "<ActionKind value>", "amount": int?}`, construct the `Action` and dispatch through `_gated_action_events`-equivalent path reusing undo/metrics/action_event (factor a small `_dispatch_matched(action, confidence, now)` helper); when the library is empty behavior is IDENTICAL to today (candidate_event only). `Daemon` exposes `command("refresh_matcher")` → `pipeline.matcher.refresh()` (also add to ipc `_COMMAND_NAMES`).
**Tests:** integration — seeded store (2 gestures × 8 exemplars + mappings to SWITCH_NEXT / SCROLL amount 2) + profile: scripted deliberate segment matching gesture A dispatches exactly one SWITCH_NEXT (visible in DryRun sink + undoable via pipeline.undo()); noise segment (unlike both classes) abstains (no dispatch, candidate abstain event); empty-library pipeline emits candidate events exactly as before (regression); refresh_matcher command works through daemon.
**Steps:** red → implement → green → full suite; `import aircontrol.app` intact.

## Self-Review
Spec Goal 1→Task 1, Goal 2→Task 2, Goal 3→Task 3, Goal 4→bench asserts in Tasks 1–2 + few-shot in Task 2. No placeholders; types (`MatchResult`, `TrajectoryMatcher`, tensor shapes) consistent across tasks; mapping action JSON matches store schema (`action` JSON text) from Spec 1.
