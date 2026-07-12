# AirControl — Spec 4: Custom Gesture Recording + Practice Arena

**Date:** 2026-07-12
**Status:** Approved for planning (scope per build direction §8–§9)
**Owner:** Rajdeep Mukherjee (orchestration: Claude; implementation: Codex)

## 0. Context
Specs 1–3 (250 tests) give us: calibrated segmentation, the DTW matcher, gated
matched-action dispatch, undo, and metrics. Custom gestures WORK if exemplars
are seeded directly into the store — but there is no user-facing way to create
them, no quality control on what gets saved, and no feedback loop. This spec
adds all three, HUD-guided like calibration (the polished UI re-skins it in
Spec 5).

## 1. Goals
1. **Recording flow** (`--record-gesture <name>`, §8): guided capture of 8–12
   repetitions auto-segmented by the calibrated `SegmentationMachine`, with
   per-take confirm/discard;
   - **Consistency check:** pairwise DTW among confirmed takes; if mean
     pairwise distance exceeds a threshold, tell the user plainly and identify
     outlier takes to re-record.
   - **Confusability check (blocking, §8.4):** (a) similarity vs every existing
     library gesture via the matcher — too close ⇒ refuse with the conflicting
     gesture named; (b) distance vs the incidental-motion density — too close
     to normal desk motion ⇒ refuse with "make it larger, slower, or more
     distinct". Never skippable in the normal flow.
   - **Save** only after both checks pass: gesture + exemplars + optional
     immediate mapping; then route into the arena.
2. **Practice arena** (`--arena`, §9): live loop showing, per candidate: what
   matched, the honest confidence number, and the runner-up. One-key
   confirm/reject:
   - confirm ⇒ take becomes a new exemplar with **diversity-aware pruning**
     (cap per gesture, drop the most redundant by pairwise DTW, never below a
     floor of the freshest takes);
   - reject ⇒ recorded as a hard negative: bumps that gesture's per-gesture
     threshold offset so its effective T1 tightens.
   - **Stress-test mode:** 60 s of "work normally"; every fired candidate is
     counted and reported as a false-fire tally — the §13 metric measured
     directly.
   - **Per-gesture health:** confirms/rejects/last-updated persisted; surfaced
     as a text summary at arena exit (dashboard UI comes in Spec 5).
3. **Store schema v2:** `gesture_stats` table (gesture_id PK/FK, confirms,
   rejects, threshold_offset, updated_at) with an idempotent v1→v2 migration;
   matcher/gate consume threshold_offset (effective T1 = base T1 + offset).

## 2. Non-Goals
Tauri/React UI (Spec 5); embedding encoder; per-app mapping contexts; skeleton
animation rendering (Spec 5); community sharing.

## 3. Components
- `curation.py` — `prune_exemplars(trajs, max_n)` diversity-aware pruning
  (greedy keep-most-spread by DTW) + `consistency_report(takes)` (mean pairwise
  distance + outlier indices).
- `recording.py` — `RecordingSession` pure state machine (mirrors
  `CalibrationRunner` shape): NAMING→CAPTURE(n takes, segmentation-driven,
  confirm/discard)→CONSISTENCY→CONFUSABILITY→SAVE|REFUSED, callbacks-free,
  feed()/advance()/confirm_take()/discard_take()/status surface.
- `arena.py` — `ArenaSession` pure logic: consume (segment, MatchResult, fired)
  tuples; confirm/reject/stress bookkeeping; store writes (exemplar add with
  pruning, stats update); summary.
- Store v2 migration + `GestureStatsRepo`.
- App/CLI wiring: `--record-gesture NAME`, `--arena [--stress]` HUD loops
  (cv2 text overlays + keys, same pattern as calibrate); daemon untouched
  except matcher refresh after saves.

## 4. Testing
Pure pytest (WSL): pruning keeps diversity (constructed clusters), consistency
flags planted outliers; recording session walk-through (scripted takes) incl.
refusal paths (too-similar to library; too-close to incidental) and the
never-skippable check; arena confirm adds exemplar + prunes at cap, reject
bumps threshold_offset and effective T1 tightens in the gate path, stress mode
counts fires; migration v1→v2 idempotent incl. on a populated v1 DB. Live
HUD flows verified by the user on Windows.
