# AirControl Calibration & Reliability (Spec 2) Implementation Plan

> **For agentic workers:** Executed by **Codex** (GPT-5.6) one task at a time, orchestrated and reviewed by Claude. Steps use checkbox (`- [ ]`) syntax. Each task ends with an independently testable deliverable; the orchestrator commits.

**Goal:** Fill the Foundation seams with reliability mechanics: gated-release fix, calibration profile + HUD-guided flow, clutch strategies, segmentation state machine, incidental-motion density (T3), undo window, and metrics/drift closure.

**Architecture:** Pure-logic modules first (profile, clutch, segmentation, density, undo) each unit-tested against synthetic sequences; then one wiring task threads them through `pipeline.py`/`daemon.py`/`app.py` and adds `--calibrate`. Engine keeps its state machine but delegates arming to the clutch strategy.

**Tech Stack:** Python 3.13 in `env/` venv, numpy, stdlib. No new deps.

## Global Constraints

- Test command: `env/bin/python -m pytest -p no:cacheprovider` (flag required).
- All 119 existing tests stay green at every task boundary; `import aircontrol.app` must keep working.
- No frames persisted ever; calibration stores landmark trajectories + statistics only.
- Loopback-only networking; no new network use.
- Style: `from __future__ import annotations`, frozen slotted dataclasses for value types, injectable clocks/seams, no ORM.
- Codex must NOT git commit (read-only index in sandbox); leave working tree for the orchestrator.
- Windows-only live behavior stays the user's verification job.

## File Structure

- Modify `src/aircontrol/pipeline.py` — Task 1 (release bypass), Task 7 (wiring).
- Create `src/aircontrol/profile.py` — CalibrationProfile value types + JSON codec (Task 2).
- Create `src/aircontrol/clutch.py` — strategy protocol + 3 implementations (Task 3).
- Modify `src/aircontrol/engine.py` — consume clutch state (Task 3).
- Create `src/aircontrol/segmentation.py` — candidate segmenter (Task 4).
- Create `src/aircontrol/density.py` — incidental kNN density (Task 5).
- Create `src/aircontrol/undo.py` — undo manager + reversibility map (Task 6).
- Create `src/aircontrol/calibration.py` — step-runner state machine (Task 7).
- Modify `src/aircontrol/config.py`, `daemon.py`, `app.py`, `cli.py` — wiring + `--calibrate` (Task 7).
- Tests: `tests/test_release_bypass.py`, `tests/test_profile.py`, `tests/test_clutch.py`, `tests/test_segmentation.py`, `tests/test_density.py`, `tests/test_undo.py`, `tests/test_calibration.py`, `tests/test_reliability_integration.py`.

---

### Task 1: Gated-release bypass fix

**Files:**
- Modify: `src/aircontrol/pipeline.py` (`_gated_action_events`)
- Test: `tests/test_release_bypass.py`

**Interfaces:**
- Produces: module-level `RELEASING_ACTIONS: frozenset[ActionKind] = frozenset({ActionKind.LEFT_UP})` in `pipeline.py`. In `_gated_action_events`, actions whose `kind` is in `RELEASING_ACTIONS` dispatch unconditionally (no gate consult, no candidate event, still `metrics.note_action()` after dispatch), producing a normal `action_event` with `confidence=1.0`.

- [ ] **Step 1: Failing test** — build a `Pipeline` with a blocking gate (`GateThresholds(t1_top1=2.0)`) exactly as `tests/test_pipeline_integration.py` does (reuse its helpers/fixtures by import or copy); script: arm engine manually (`pipeline.engine.manual_toggle(t)` then dispatch through `_forced_action_events` is NOT the path — instead simulate a pinch: drive observations that enter PINCH so `LEFT_DOWN` would be gated) — simpler deterministic approach: call `pipeline._gated_action_events([Action(ActionKind.LEFT_DOWN)], now=1.0)` → expect NO `left_down` in sink events (gated); then `pipeline._gated_action_events([Action(ActionKind.LEFT_UP)], now=2.0)` → expect `left_up` DID dispatch despite the blocking gate.
- [ ] **Step 2: Run, verify fail** (`LEFT_UP` currently suppressed).
- [ ] **Step 3: Implement** the `RELEASING_ACTIONS` bypass.
- [ ] **Step 4: Full suite green.**
- [ ] **Step 5: Orchestrator commits** — `fix(pipeline): releasing actions bypass the confidence gate`.

---

### Task 2: Calibration profile value types (`profile.py`)

**Files:**
- Create: `src/aircontrol/profile.py`
- Test: `tests/test_profile.py`

**Interfaces (all frozen slotted dataclasses):**
- `InteractionVolume(x_min: float, x_max: float, y_min: float, y_max: float)` with `.contains(x: float, y: float) -> bool` (inclusive bounds).
- `MotionSignature(velocity_floor: float, velocity_ceiling: float)` — palm-normalized units/second; validate `0 < floor < ceiling`.
- `LightingProfile(mean_brightness: float, landmark_jitter: float, acceptable: bool)`.
- `CalibrationProfile(hand_size: float, volume: InteractionVolume, motion: MotionSignature, lighting: LightingProfile, incidental_features: tuple[tuple[float, ...], ...], created_at: float)` — `incidental_features` is the fitted density's training matrix (rows = feature vectors, Task 5 defines the featurizer).
- `to_payload(profile: CalibrationProfile) -> dict` / `from_payload(payload: dict) -> CalibrationProfile` — round-trip exact; raise `ValueError` on missing/invalid keys; payload versioned `{"v": 1, ...}`.
- `save_profile(store: Store, profile: CalibrationProfile, name: str = "default") -> None` (saves active via `store.calibration.save(name, payload, active=True)`); `load_active_profile(store: Store) -> CalibrationProfile | None`.

- [ ] **Step 1: Failing tests** — construction validation (floor<ceiling enforced, hand_size>0), `contains` truth table, payload round-trip equality, `from_payload` rejects wrong `v`/missing keys, save/load through an in-memory `Store(":memory:")` returns an equal profile, `load_active_profile` returns `None` on fresh store.
- [ ] **Step 2–4:** fail → implement → full suite green.
- [ ] **Step 5: Orchestrator commits** — `feat(profile): calibration profile types + store codec`.

---

### Task 3: Clutch strategies (`clutch.py`) + engine delegation

**Files:**
- Create: `src/aircontrol/clutch.py`
- Modify: `src/aircontrol/engine.py`, `src/aircontrol/config.py`
- Test: `tests/test_clutch.py`

**Interfaces:**
- `@dataclass ClutchState(armed: bool, progress: float, status_text: str)`.
- `ClutchStrategy` protocol: `update(sample: GestureSample | None, now: float) -> ClutchState`; `reset() -> None`; `notify_gesture(now: float) -> None` (resets the arm window).
- `WakePoseClutch(config: GestureConfig, volume: InteractionVolume | None, *, hold_seconds: float = 0.4, window_seconds: float = 5.0)` — arms after OPEN_PALM held `hold_seconds` with center inside `volume` (volume `None` = anywhere, pre-calibration); disarms after `window_seconds` without `notify_gesture`, or FIST held `config.pause_hold_seconds`. Reuses the motion-stability rule from the current engine `_handle_safety_hold` (movement > `hold_motion_limit_palms` restarts the hold).
- `SpatialZoneClutch(plane_y: float)` — armed whenever the sample center is above (`y <`) the plane; no hold needed.
- `AlwaysOnClutch()` — always armed.
- `build_clutch(config: AppConfig, profile: CalibrationProfile | None) -> ClutchStrategy` — from new `ClutchConfig`.
- `config.py`: add `ClutchConfig(mode: str = "wake_pose", hold_seconds: float = 0.4, window_seconds: float = 5.0, plane_y: float = 0.5, acknowledged_expert_mode: bool = False)` on `AppConfig` as `clutch`; validation: mode in `{"wake_pose","spatial_zone","always_on"}`; `always_on` REQUIRES `acknowledged_expert_mode=True` else `ValueError`.
- `engine.py`: `GestureEngine.__init__` gains optional `clutch: ClutchStrategy | None = None`. When `clutch` is provided, `update()` sets `self.armed` from `clutch.update(sample, now)` (calling `_set_armed` on transitions so exits/cleanup still run, and calling `clutch.notify_gesture(now)` whenever an active gesture completes) and skips `_handle_safety_hold`. When `clutch is None`, behavior is EXACTLY today's (all existing engine tests must pass unchanged).

- [ ] **Step 1: Failing tests** — wake pose arms after hold (scripted samples with timestamps), motion resets hold, window timeout disarms, `notify_gesture` extends, fist disarms; spatial zone arms above/disarms below plane; always-on always armed; `build_clutch` mode selection + expert-flag refusal (ValueError from config validation); engine-with-wake-pose-clutch arms/disarms through `update()` while engine-without-clutch matches legacy behavior (reuse scripts from `tests/test_engine.py`).
- [ ] **Step 2–4:** fail → implement → full suite green (including untouched legacy engine tests).
- [ ] **Step 5: Orchestrator commits** — `feat(clutch): pluggable clutch strategies with wake-pose default`.

---

### Task 4: Segmentation state machine (`segmentation.py`)

**Files:**
- Create: `src/aircontrol/segmentation.py`
- Test: `tests/test_segmentation.py`

**Interfaces:**
- `@dataclass CandidateSegment(trajectory: Trajectory, t_onset: float, t_offset: float)`.
- `SegmentationMachine(motion: MotionSignature, *, onset_frames: int = 3, offset_frames: int = 4, max_duration: float = 2.5, presence_frames: int = 5, hysteresis: float = 0.8)`:
  - `.update(frame: LandmarkFrame | None, armed: bool, now: float) -> CandidateSegment | None`.
  - Internal velocity: palm-normalized center displacement between consecutive frames / dt (center = mean of landmarks 0,5,9,13,17; palm size = ‖lm9−lm0‖ per frame with `1e-9` floor).
  - States: WAITING (not armed or hand absent) → PRESENT (hand seen `presence_frames` consecutive frames — entry debounce) → ONSET counting (velocity ≥ `motion.velocity_floor` for `onset_frames` consecutive) → IN_GESTURE (buffer frames from first onset frame) → offset when velocity < `motion.velocity_floor * hysteresis` for `offset_frames` consecutive, OR duration > `max_duration` (then the segment is DISCARDED, not emitted — over-long motions are rejected) → emit `CandidateSegment` and return to PRESENT.
  - Hand disappearing (frame None) at ANY state → discard any partial segment, return to WAITING (exit debounce guarantee: entering/leaving frame never emits).
  - `reset()`.

- [ ] **Step 1: Failing tests** — synthetic frame scripts (helper generating frames with controlled center velocity): deliberate burst emits exactly one segment with correct onset/offset ordering and trajectory length; sub-floor jitter never emits; hysteresis (velocity dipping between floor*0.8 and floor doesn't end the gesture prematurely); >2.5 s motion discarded; hand entry (first `presence_frames` frames) never counts toward onset; mid-gesture disappearance discards; not-armed never emits.
- [ ] **Step 2–4:** fail → implement → full suite green.
- [ ] **Step 5: Orchestrator commits** — `feat(segmentation): calibrated temporal segmentation state machine`.

---

### Task 5: Incidental-motion density (`density.py`)

**Files:**
- Create: `src/aircontrol/density.py`
- Test: `tests/test_density.py`

**Interfaces:**
- `featurize(traj: Trajectory) -> tuple[float, ...]` — fixed-length feature vector from `normalize(traj)`: 8-bin histogram of per-frame speed (‖velocity‖ summed over landmarks, bins spaced on `[0, 2]` in normalized units, values clipped), + total path length of the wrist-relative centroid, + duration seconds, + mean & max speed → **12 floats**. Deterministic; length constant `FEATURE_DIM = 12`.
- `IncidentalDensity` with `fit(cls, trajectories: Sequence[Trajectory], k: int = 5) -> IncidentalDensity` (stores featurized matrix, per-dim mean/std standardization; k capped at n−1 with floor 1) and `.distance(traj: Trajectory) -> float` — mean Euclidean distance in standardized feature space to the k nearest training rows. Empty/insufficient training (`n < 2`) → `fit` raises `ValueError`.
- `.to_rows() -> tuple[tuple[float, ...], ...]` / `IncidentalDensity.from_rows(rows, k=5)` for persistence inside `CalibrationProfile.incidental_features` (store standardization params in rows[0:2] or as separate fields — implementer's choice, but round-trip must reproduce identical distances).

- [ ] **Step 1: Failing tests** — featurize returns 12 floats, deterministic; fit/distance: cluster of slow-jitter trajectories → a similar slow trajectory scores LOW distance, a fast large-amplitude swipe scores HIGH (assert ratio > 3×); round-trip `to_rows`/`from_rows` preserves distances exactly; `fit` raises on 0/1 trajectories.
- [ ] **Step 2–4:** fail → implement → full suite green.
- [ ] **Step 5: Orchestrator commits** — `feat(density): incidental-motion kNN density for T3`.

---

### Task 6: Undo manager (`undo.py`)

**Files:**
- Create: `src/aircontrol/undo.py`
- Test: `tests/test_undo.py`

**Interfaces:**
- `REVERSIBLE: dict[ActionKind, Callable[[Action], Action]]` mapping:
  `SWITCH_NEXT → Action(SWITCH_PREVIOUS)`, `SWITCH_PREVIOUS → Action(SWITCH_NEXT)`, `SCROLL → Action(SCROLL, amount=-a.amount)`, `SHOW_DESKTOP → Action(SHOW_DESKTOP)`; plus `TASK_VIEW` reversal = a NEW ActionKind `ESCAPE` (add `ESCAPE = "escape"` to `ActionKind` in `domain.py`, dispatch in `controller.py` via a new `InputSink.hotkey` call with `VK_ESCAPE = 0x1B` added to `input_sink.py` key names; DryRun path needs nothing special).
- `UndoManager(metrics: Metrics, *, window_seconds: float = 3.0, clock=time.monotonic)`:
  - `.note_fire(action: Action) -> None` (remembers last fired reversible action + time; irreversible actions clear the slot),
  - `.try_undo() -> Action | None` — inside the window returns the inverse action, records `metrics.note_false_positive()`, clears the slot; outside window/empty → None (still nothing logged).

**Files also modified:** `src/aircontrol/domain.py` (ESCAPE kind), `src/aircontrol/controller.py` (dispatch ESCAPE → `sink.hotkey(VK_ESCAPE)`, description "UNDO · ESC"), `src/aircontrol/input_sink.py` (add `VK_ESCAPE` constant + name entry).

- [ ] **Step 1: Failing tests** — each mapping inverse correct (incl. SCROLL amount negation and TASK_VIEW→ESCAPE); window expiry (fake clock) returns None; try_undo logs exactly one FP and clears (second call None); irreversible fire (e.g. MOVE_POINTER) clears pending undo; controller dispatches ESCAPE through a DryRun sink with a hotkey event recorded.
- [ ] **Step 2–4:** fail → implement → full suite green.
- [ ] **Step 5: Orchestrator commits** — `feat(undo): 3-second undo window with FP logging`.

---

### Task 7: Calibration flow + full wiring

**Files:**
- Create: `src/aircontrol/calibration.py`
- Modify: `src/aircontrol/config.py` (`CalibrationConfig(negative_seconds: float = 30.0, snapshot_seconds: float = 3.0, motion_reps: int = 2)` as `AppConfig.calibration`), `src/aircontrol/pipeline.py`, `src/aircontrol/daemon.py`, `src/aircontrol/app.py`, `src/aircontrol/cli.py` (`--calibrate` flag)
- Test: `tests/test_calibration.py`, `tests/test_reliability_integration.py`

**Interfaces:**
- `calibration.py`: `CalibrationRunner(config: AppConfig, *, clock=time.monotonic)` — a step state machine with `current_step() -> StepInfo(name: str, instruction: str, progress: float, recording: bool)`, `advance() -> None` (user pressed key), `feed(observation: HandObservation | None, frame_brightness: float | None, now: float) -> None`, `is_complete() -> bool`, `result() -> CalibrationProfile` (raises until complete). Steps in order: `framing` (collects hand-center bounding box while user moves hand around → `InteractionVolume` padded 10%), `hand_snapshot` (collects `hand_size` median over `snapshot_seconds` of OPEN_PALM frames), `motion_signature` (captures per-frame velocities during `motion_reps` user swipes → floor = 25th percentile of the slow rep's peak, ceiling = fast rep's peak; store as `MotionSignature(floor=0.25*slow_peak … )` — exact rule: `velocity_floor = 0.3 * min(peaks)`, `velocity_ceiling = 1.2 * max(peaks)`), `negative_capture` (records ALL frames for `negative_seconds` into trajectories chunked every 2 s → featurized via Task 5 → `incidental_features`), `lighting` (mean brightness from supplied `frame_brightness` + landmark jitter = std of hand-center when nominally still; `acceptable = brightness in [40, 220] and jitter < 0.05`).
- `pipeline.py` wiring: `Pipeline.__init__` gains `profile: CalibrationProfile | None = None`; builds clutch via `build_clutch(config, profile)` passed into `GestureEngine`; builds `SegmentationMachine` when profile present (else dormant); each `process()` feeds segmentation with the appended frame + armed state, and on `CandidateSegment` calls `metrics.note_candidate(armed=...)` and — when a density model exists — emits a `candidate_event` whose gate decision uses `gate.evaluate(top1=1.0, top2=0.0, incidental_distance=density.distance(segment.trajectory))` (NO dispatch from segments this spec; heuristic engine still drives actions). `UndoManager` wraps `_gated_action_events` dispatches (`note_fire`), and a new `Pipeline.undo() -> list[PipelineEvent]` dispatches `try_undo()`'s action through `_forced_action_events`.
- `daemon.py`: `command("undo")` → `pipeline.undo()`; IPC `_COMMAND_NAMES` in `ipc.py` gains `"undo"`; daemon loads the active profile from the store at startup (`load_active_profile`) and passes it to `Pipeline`; status events gain no schema change (v stays 1; `recalibration_recommended` deferred to the drift task below if trivial — include boolean in status_event ONLY if `ipc.status_event` signature is extended with a default so existing calls stay valid).
- `app.py`: Esc key in the main loop calls `daemon.command("undo")` FIRST; only quits if no undo was pending (undo returns empty events → quit). `--calibrate`: separate loop rendering `CalibrationRunner.current_step()` text over the camera preview (reuse overlay's text drawing or plain `cv2.putText`), Space advances, saves profile on completion via `save_profile`, prints summary, exits.
- `cli.py`: `--calibrate` flag routed to the calibration loop.

- [ ] **Step 1: Failing tests** —
  - `test_calibration.py`: scripted runner walk-through with synthetic observations completes all 5 steps and yields a valid profile (volume contains scripted centers, hand_size ≈ scripted, floor < ceiling, ≥ 5 incidental feature rows, lighting acceptable given scripted brightness); advancing without required data keeps `is_complete() False`; `result()` raises before completion.
  - `test_reliability_integration.py`: pipeline with a fitted profile: (a) unclutched scripted desk-work sequence → zero dispatched actions AND zero fired candidate events; (b) wake-pose arm then a deliberate swipe → segmentation emits ≥1 candidate (visible via metrics snapshot candidates count) while heuristic actions still dispatch; (c) `daemon.command("undo")` within 3 s of a SWITCH_NEXT fire dispatches SWITCH_PREVIOUS and `metrics.snapshot().fp_per_hour > 0`; (d) after window expiry undo dispatches nothing.
- [ ] **Step 2–4:** fail → implement → full suite green; `import aircontrol.app` OK; `python -m aircontrol --help` shows `--calibrate`.
- [ ] **Step 5: Orchestrator commits** — `feat(calibration): HUD-guided calibration flow + reliability wiring`.

---

## Self-Review

**Spec coverage:** Goal 1→Task 1; Goal 2→Tasks 2, 7; Goal 3→Task 3; Goal 4→Task 4; Goal 5→Task 5; Goal 6→Task 6 (+ESCAPE plumbing); Goal 7→Task 7 (candidates/FP through metrics; jitter-drift flag deliberately minimal — lighting jitter captured at calibration; live drift detector deferred if `status_event` extension proves invasive, noted for Spec 3). Resolved decisions §2 all embodied (HUD flow Task 7, wake-pose default Task 3, undo map Task 6, expert flag Task 3).
**Placeholder scan:** none — every task has exact interfaces, rules, and test intents; numeric rules pinned (0.3×min-peak floor, 1.2×max-peak ceiling, 12-dim features, 10% volume pad, [40,220] brightness).
**Type consistency:** `MotionSignature`/`InteractionVolume` defined Task 2, consumed Tasks 3/4/7; `CandidateSegment` Task 4→7; `featurize`/`FEATURE_DIM` Task 5→7; `UndoManager` Task 6→7; `ESCAPE` kind defined Task 6 and used only there+controller.
