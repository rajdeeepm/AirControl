# AirControl — Spec 3: DTW Matcher Core

**Date:** 2026-07-11
**Status:** Approved for planning (scope per build direction §7 + Phase 2 risk note:
"build the DTW baseline first; if it wins, ship it and revisit")
**Owner:** Rajdeep Mukherjee (orchestration: Claude; implementation: Codex)

## 0. Context

Specs 1–2 are code-complete (232 tests): segmentation emits `CandidateSegment`s,
the T1/T2/T3 gate has its incidental-distance input from the kNN density, and the
store persists per-gesture exemplar trajectories. What is missing is the matcher:
given a candidate segment and a library of user gesture exemplars, decide WHICH
gesture (or abstain). The build direction prescribes trying dynamic-time-warping
nearest-neighbor before any learned encoder; the embedding model becomes a later
drop-in behind the same interface if DTW falls short.

## 1. Goal

1. **`dtw.py`:** DTW distance between two `NormalizedTrajectory` canonical tensors
   (Sakoe–Chiba band constraint for O(T·band) cost; per-frame cost = mean Euclidean
   distance across the 21 landmarks; velocity channel weighted in). Pure numpy,
   deterministic, unit under 10 ms for two 45-frame trajectories.
2. **`matcher.py`:** `TrajectoryMatcher` protocol (the seam the future encoder
   implements too) + `DtwMatcher`:
   - built from the store's gesture exemplars (5–15 per gesture, refreshable);
   - `match(segment) -> MatchResult(gesture_id | None, top1: float, top2: float,
     scores: dict)` where scores are similarities in [0, 1] mapped from DTW
     distances (e.g. `1 / (1 + d)`);
   - top1/top2 feed `gate.evaluate(top1, top2, incidental_distance)` directly —
     the gate remains the single decision authority.
3. **Pipeline integration:** when a profile AND a non-empty gesture library exist,
   matched-and-gate-fired candidates dispatch the action mapped in `store.mappings`
   (context `global`) via the existing dispatch path (undo + metrics included).
   The heuristic engine keeps owning the built-in vocabulary; matcher-dispatched
   actions are additive and OFF until the library has gestures (so current behavior
   is unchanged for the user today).
4. **Bench harness:** a pytest-marked benchmark asserting the latency budget and a
   synthetic few-shot evaluation (N gesture classes from generators with intra-class
   jitter; assert top-1 accuracy and abstention on held-out noise) — the yardstick
   the future encoder must beat to earn its complexity.

## 2. Non-Goals
Dataset download/extraction, encoder training, ONNX (deferred until DTW is beaten);
custom-gesture RECORDING UI (Spec 4 — tests seed exemplars directly via the store);
per-app mapping contexts.

## 3. Testing
Pure pytest in WSL: DTW properties (identity=0, symmetry, band correctness, known
warps rank closer than different shapes); matcher few-shot accuracy + margin
behavior + empty-library abstention; integration: seeded store (2 gestures ×
8 exemplars) + profile → scripted segment dispatches the mapped action once,
noise segment abstains silently; latency benchmark under budget.
