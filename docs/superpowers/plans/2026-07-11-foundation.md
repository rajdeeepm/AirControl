# AirControl Foundation (Spec 1) Implementation Plan

> **For agentic workers:** This plan is executed by **Codex** (GPT-5.6), one task at a time, orchestrated and reviewed by Claude. Steps use checkbox (`- [ ]`) syntax for tracking. Each task ends with an independently testable deliverable and a commit.

**Goal:** Build the architectural + data spine (trajectory representation, rolling buffer, SQLite persistence, daemon/IPC boundary, metrics, confidence-gate seam) and port the existing engine behind it — keeping a runnable app and all tests green.

**Architecture:** Additive-first. New pure/testable modules land alongside the existing heuristic pipeline; then a `Pipeline`/`Daemon` object threads them together (recognize → trajectory buffer → engine → gate → controller, with metrics + store), and `app.py` becomes a thin HUD client that embeds the daemon. The future UI (Spec 5) attaches over a loopback WebSocket.

**Tech Stack:** Python 3.13 (floor 3.11), `numpy`, stdlib `sqlite3`, `websockets` (new dep), `pytest`. Existing: MediaPipe (lazy), OpenCV.

## Global Constraints

- Python `>=3.11`; developed/tested on 3.13 in the in-repo Linux venv `env/` (`env/bin/python`). Run tests with `python -m pytest -p no:cacheprovider` (the repo's `.pytest_cache` dir has locked perms).
- **No camera frames are ever written to disk or into any persisted/serialized structure.** Only 21-landmark skeleton coordinates. This is a test-enforced invariant.
- All networking is **loopback only** (`127.0.0.1`); the daemon makes no outbound connections.
- The DB file lives in a per-user app-data dir by default — **never** under version control; tests use temp files.
- Keep the existing **61 tests green** at every task boundary; `python -m aircontrol --practice` must still launch.
- Dispatch stays **Windows-only** this spec. Logic verified in WSL; live camera/`SendInput` verified by the user on Windows.
- Mapping data model is `gesture × context → action` with v1 `context = 'global'` (so per-app profiles are later a UI change, not a migration).
- Follow existing code style: `from __future__ import annotations`, frozen slotted dataclasses for value types, injectable seams for testability, no ORM.
- New runtime deps go in `pyproject.toml`; `psutil` is **optional** (degrade gracefully if absent).

## File Structure

- Create `src/aircontrol/trajectory.py` — landmark-trajectory value types, normalization, (de)serialization. Pure; `numpy` only.
- Create `src/aircontrol/buffer.py` — bounded rolling frame buffer.
- Create `src/aircontrol/store.py` — SQLite persistence (schema, migrations, repositories).
- Create `src/aircontrol/metrics.py` — instrumentation harness (fake-clock friendly).
- Create `src/aircontrol/gate.py` — confidence-gate abstention seam.
- Create `src/aircontrol/pipeline.py` — UI-agnostic per-observation pipeline (recognize→buffer→engine→gate→dispatch + metrics).
- Create `src/aircontrol/ipc.py` — loopback WebSocket server, client, in-process fake, message schema.
- Create `src/aircontrol/daemon.py` — lifecycle object wrapping the pipeline + optional IPC server.
- Modify `src/aircontrol/config.py` — add `PipelineConfig`, `StoreConfig`, `MetricsConfig`, `IpcConfig` sections + validation.
- Modify `src/aircontrol/app.py` — feed observations to the daemon/pipeline; keep HUD rendering.
- Modify `src/aircontrol/domain.py` — add `confidence` to dispatched actions' reporting if needed (see Task 5).
- Modify `pyproject.toml` — add `websockets`; `dev` extras add `numpy`, `pytest`, `opencv-contrib-python-headless`.
- Tests: one `tests/test_<module>.py` per new module + `tests/test_pipeline_integration.py`.

---

### Task 1: Trajectory representation & normalization (`trajectory.py`)

**Files:**
- Create: `src/aircontrol/trajectory.py`
- Test: `tests/test_trajectory.py`

**Interfaces:**
- Consumes: `aircontrol.domain.Point3D`, `HandObservation`.
- Produces:
  - `LandmarkFrame(landmarks: tuple[Point3D, ...], handedness: str, timestamp: float)` — `landmarks` length 21.
  - `Trajectory(frames: tuple[LandmarkFrame, ...], handedness: str)`.
  - `NormalizedTrajectory(canonical: np.ndarray, orientation: np.ndarray, velocity: np.ndarray, frame_count: int)` — `canonical` shape `(T, 21, 3)`, `orientation` shape `(T, 3, 3)` (per-frame palm basis), `velocity` shape `(T, 21, 3)`.
  - `frame_from_observation(obs: HandObservation, timestamp: float) -> LandmarkFrame`
  - `hand_size(frame: LandmarkFrame) -> float` — Euclidean distance landmark 0→9.
  - `normalize(traj: Trajectory, *, resample_length: int = 45, hand_size: float | None = None) -> NormalizedTrajectory`
  - `serialize(traj: Trajectory) -> bytes` and `deserialize(blob: bytes, handedness: str) -> Trajectory` — float32; array shape `(T, 64)` = 63 landmark coords + timestamp.

**Normalization algorithm (implement exactly):**
1. Build a `(N, 21, 3)` float64 array from frames and a length-`N` timestamps array.
2. **Translate:** subtract landmark 0 (wrist) from all landmarks, per frame.
3. **Scale:** divide all coords by `hand_size` (arg if given, else the mean over frames of `‖lm9 − lm0‖` after translation; guard against 0 with a `1e-9` floor).
4. **Rotate to canonical (per frame):** build an orthonormal basis `R` (3×3) from `x_axis = normalize(lm5 − lm0)` (index MCP dir), `tmp = lm17 − lm0` (pinky MCP dir), `z_axis = normalize(cross(x_axis, tmp))`, `y_axis = cross(z_axis, x_axis)`; stack rows `[x_axis, y_axis, z_axis]`. Rotate landmarks: `canon = landmarks @ R.T`. Store `R` per frame as the `orientation` channel (the raw palm orientation kept in parallel, per §3).
5. **Resample:** linearly interpolate `canon` along time from `N` frames to `T = resample_length` using normalized time in `[0, 1]` (from timestamps if strictly increasing, else uniform frame index). Same interpolation applied to build the resampled orientation via SLERP-free per-element interpolation is acceptable (interpolate the 3×3 then re-orthonormalize each frame with Gram–Schmidt).
6. **Velocity:** finite difference of resampled `canonical` along time: `velocity[0] = 0`, `velocity[t] = canonical[t] − canonical[t-1]`.
7. Cast `canonical`, `orientation`, `velocity` to `float32`. `frame_count = N`.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_trajectory.py
from __future__ import annotations
import numpy as np
from aircontrol.domain import Point3D
from aircontrol.trajectory import (
    LandmarkFrame, Trajectory, normalize, hand_size, serialize, deserialize,
)

def _straight_hand(dx=0.0, dy=0.0, scale=1.0, t=0.0):
    # 21 landmarks laid out deterministically; wrist=0, middle-MCP=9 one unit up.
    pts = []
    for i in range(21):
        pts.append(Point3D(x=(i * 0.01) * scale + dx, y=(i * 0.02) * scale + dy, z=0.0))
    pts[0] = Point3D(dx, dy, 0.0)
    pts[9] = Point3D(dx, dy + 1.0 * scale, 0.0)
    pts[5] = Point3D(dx + 0.5 * scale, dy + 0.3 * scale, 0.0)
    pts[17] = Point3D(dx - 0.5 * scale, dy + 0.3 * scale, 0.0)
    return LandmarkFrame(landmarks=tuple(pts), handedness="Right", timestamp=t)

def test_hand_size_is_wrist_to_middle_mcp():
    assert abs(hand_size(_straight_hand(scale=2.0)) - 2.0) < 1e-6

def test_translation_invariance():
    a = normalize(Trajectory((_straight_hand(t=0.0), _straight_hand(t=0.1)), "Right"))
    b = normalize(Trajectory((_straight_hand(dx=5.0, t=0.0), _straight_hand(dx=5.0, t=0.1)), "Right"))
    assert np.allclose(a.canonical, b.canonical, atol=1e-5)

def test_scale_invariance():
    a = normalize(Trajectory((_straight_hand(scale=1.0, t=0.0), _straight_hand(scale=1.0, t=0.1)), "Right"))
    b = normalize(Trajectory((_straight_hand(scale=3.0, t=0.0), _straight_hand(scale=3.0, t=0.1)), "Right"))
    assert np.allclose(a.canonical, b.canonical, atol=1e-4)

def test_resample_to_fixed_length():
    frames = tuple(_straight_hand(t=i * 0.05) for i in range(10))
    out = normalize(Trajectory(frames, "Right"), resample_length=45)
    assert out.canonical.shape == (45, 21, 3)
    assert out.velocity.shape == (45, 21, 3)
    assert out.orientation.shape == (45, 3, 3)
    assert out.frame_count == 10
    assert np.allclose(out.velocity[0], 0.0)

def test_serialize_roundtrip_is_exact_in_float32():
    traj = Trajectory((_straight_hand(t=0.0), _straight_hand(dx=1.0, t=0.1)), "Right")
    back = deserialize(serialize(traj), "Right")
    assert back.handedness == "Right"
    assert len(back.frames) == 2
    for f0, f1 in zip(traj.frames, back.frames):
        for p0, p1 in zip(f0.landmarks, f1.landmarks):
            assert abs(np.float32(p0.x) - p1.x) < 1e-6
        assert abs(np.float32(f0.timestamp) - f1.timestamp) < 1e-6

def test_no_pixel_fields_present():
    # Privacy invariant: value types expose only landmark/handedness/time.
    fields = set(LandmarkFrame.__dataclass_fields__)
    assert fields == {"landmarks", "handedness", "timestamp"}
```

- [ ] **Step 2: Run tests, verify they fail** — `python -m pytest -p no:cacheprovider tests/test_trajectory.py` → FAIL (module missing).
- [ ] **Step 3: Implement `trajectory.py`** per the algorithm above.
- [ ] **Step 4: Run tests, verify pass.**
- [ ] **Step 5: Commit** — `git add src/aircontrol/trajectory.py tests/test_trajectory.py && git commit -m "feat(trajectory): landmark trajectory representation + normalization"`

---

### Task 2: Rolling frame buffer (`buffer.py`)

**Files:**
- Create: `src/aircontrol/buffer.py`
- Test: `tests/test_buffer.py`

**Interfaces:**
- Consumes: `aircontrol.trajectory.LandmarkFrame`, `Trajectory`.
- Produces:
  - `RollingFrameBuffer(capacity: int)` with `append(frame: LandmarkFrame) -> None`, `__len__`, `last(n: int) -> Trajectory`, `since(timestamp: float) -> Trajectory`, `clear() -> None`.
  - `last`/`since` return a `Trajectory` whose `handedness` is the most recent frame's handedness (or `"Unknown"` if empty).

**Behavior:** fixed capacity; appending beyond capacity evicts oldest (use `collections.deque(maxlen=capacity)`). `last(n)` returns up to the last `n` frames. `since(t)` returns frames with `timestamp >= t`.

- [ ] **Step 1: Write failing tests** (capacity eviction; `last(n)` count and order; `since` filter; empty → empty `Trajectory` with `"Unknown"`).

```python
# tests/test_buffer.py
from __future__ import annotations
from aircontrol.domain import Point3D
from aircontrol.trajectory import LandmarkFrame
from aircontrol.buffer import RollingFrameBuffer

def _frame(t):
    return LandmarkFrame(landmarks=tuple(Point3D(0, 0, 0) for _ in range(21)), handedness="Right", timestamp=t)

def test_capacity_evicts_oldest():
    buf = RollingFrameBuffer(capacity=3)
    for i in range(5):
        buf.append(_frame(float(i)))
    assert len(buf) == 3
    assert [f.timestamp for f in buf.last(3).frames] == [2.0, 3.0, 4.0]

def test_since_filters_by_timestamp():
    buf = RollingFrameBuffer(capacity=10)
    for i in range(5):
        buf.append(_frame(float(i)))
    assert [f.timestamp for f in buf.since(2.0).frames] == [2.0, 3.0, 4.0]

def test_empty_returns_unknown_trajectory():
    buf = RollingFrameBuffer(capacity=3)
    traj = buf.last(3)
    assert traj.frames == () and traj.handedness == "Unknown"
```

- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement `buffer.py`.**
- [ ] **Step 4: Run, verify pass.**
- [ ] **Step 5: Commit** — `git commit -m "feat(buffer): bounded rolling landmark-frame buffer"`

---

### Task 3: SQLite persistence (`store.py`)

**Files:**
- Create: `src/aircontrol/store.py`
- Test: `tests/test_store.py`

**Interfaces:**
- Consumes: `aircontrol.trajectory.serialize/deserialize`, `Trajectory`.
- Produces:
  - `Store(path: str | Path)` — opens/creates DB, runs migrations, WAL mode; `close()`; context-manager.
  - Repositories accessible as attributes: `store.gestures`, `store.exemplars`, `store.mappings`, `store.calibration`.
  - `GestureRecord(id, name, description, created_at, updated_at)`, `MappingRecord(id, gesture_id, context, action, enabled, created_at)`, `CalibrationRecord(id, name, payload, active)`.
  - `GestureRepo.add(name, description="") -> GestureRecord`, `.get(id)`, `.list()`, `.rename(id, name)`, `.delete(id)`.
  - `ExemplarRepo.add(gesture_id, traj: Trajectory) -> int`, `.list(gesture_id) -> list[Trajectory]`, `.count(gesture_id) -> int`, `.delete(id)`.
  - `MappingRepo.set(gesture_id, action: dict, context="global", enabled=True) -> MappingRecord`, `.list(context="global")`, `.for_gesture(gesture_id, context="global")`.
  - `CalibrationRepo.save(name, payload: dict, active=False) -> CalibrationRecord`, `.active() -> CalibrationRecord | None`, `.list()`.
  - `Store.delete_everything() -> None` — clears all rows in all tables (the §12 guarantee).
  - `Store.schema_version -> int`.

**Schema (v1) — create in a `_migrate` that reads/writes a `schema_version` table and is idempotent:**
```sql
CREATE TABLE schema_version (version INTEGER NOT NULL);
CREATE TABLE gestures (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE,
  description TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL, updated_at REAL NOT NULL);
CREATE TABLE exemplars (id INTEGER PRIMARY KEY, gesture_id INTEGER NOT NULL,
  trajectory BLOB NOT NULL, frame_count INTEGER NOT NULL, created_at REAL NOT NULL,
  FOREIGN KEY (gesture_id) REFERENCES gestures(id) ON DELETE CASCADE);
CREATE TABLE mappings (id INTEGER PRIMARY KEY, gesture_id INTEGER NOT NULL,
  context TEXT NOT NULL DEFAULT 'global', action TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
  created_at REAL NOT NULL, UNIQUE(gesture_id, context),
  FOREIGN KEY (gesture_id) REFERENCES gestures(id) ON DELETE CASCADE);
CREATE TABLE calibration_profiles (id INTEGER PRIMARY KEY, name TEXT NOT NULL,
  payload TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL);
```
Enable `PRAGMA foreign_keys=ON` and `PRAGMA journal_mode=WAL`. `action`/`payload` stored as JSON text. `trajectory` stored via `serialize(traj)`; `ExemplarRepo.list` returns `deserialize`d `Trajectory`s (handedness read from a stored column — add `handedness TEXT` to exemplars).

- [ ] **Step 1: Write failing tests** covering: fresh DB reports `schema_version == 1`; migration idempotent (opening twice is safe); gesture add/get/list/rename/delete; exemplar add/list round-trips a `Trajectory`; FK cascade (deleting a gesture removes its exemplars/mappings); mapping `context` defaults to `'global'` and `UNIQUE(gesture_id, context)` upserts; calibration save + `active()`; `delete_everything()` empties all tables. Use `tmp_path` fixtures.
- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement `store.py`** (add `handedness` column to `exemplars`).
- [ ] **Step 4: Run, verify pass.**
- [ ] **Step 5: Commit** — `git commit -m "feat(store): SQLite persistence for gestures/exemplars/mappings/calibration"`

---

### Task 4: Metrics harness (`metrics.py`)

**Files:**
- Create: `src/aircontrol/metrics.py`
- Test: `tests/test_metrics.py`

**Interfaces:**
- Produces:
  - `Metrics(clock: Callable[[], float] = time.monotonic)`.
  - `.note_candidate(armed: bool) -> None`
  - `.note_false_positive() -> None`
  - `latency timer:` `.begin_gesture() -> None` (marks motion offset) and `.note_action() -> None` (records `now - last_begin` in ms); percentile via `.snapshot()`.
  - `.sample_cpu() -> None` (uses `psutil` if importable else no-op).
  - `.snapshot() -> MetricsSnapshot` with fields `candidates_per_hour: float`, `armed_candidates_per_hour: float`, `fp_per_hour: float`, `latency_ms_p50: float`, `latency_ms_p95: float`, `cpu_pct: float`, `uptime_seconds: float`.
  - `.export_json() -> str` (local, aggregate-only).
- Rates computed as `count / max(uptime_hours, tiny)`; percentiles from a bounded latency list (cap length, drop oldest).

- [ ] **Step 1: Write failing tests** with a fake clock list-driven callable: candidate rate math (e.g., 2 candidates at uptime 0.5h → 4.0/hr); latency percentiles from known values; FP rate; `sample_cpu` no-ops without psutil; `export_json` parses back to the snapshot fields.
- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement `metrics.py`.**
- [ ] **Step 4: Run, verify pass.**
- [ ] **Step 5: Commit** — `git commit -m "feat(metrics): local instrumentation harness"`

---

### Task 5: Confidence-gate seam (`gate.py`) + dispatch wiring

**Files:**
- Create: `src/aircontrol/gate.py`
- Test: `tests/test_gate.py`

**Interfaces:**
- Produces:
  - `@dataclass GateDecision(fire: bool, confidence: float, reason: str)`
  - `@dataclass GateThresholds(t1_top1: float = 0.0, t2_margin: float = 0.0, t3_incidental: float = 0.0)` — permissive defaults = always fire (preserves current behavior).
  - `ConfidenceGate(thresholds: GateThresholds)` with `.evaluate(top1: float, top2: float, incidental_distance: float) -> GateDecision`.
  - Decision rule: `fire = (top1 >= t1) and (top1 - top2 >= t2) and (incidental_distance >= t3)`; `confidence = top1`; `reason` names the first failing gate (`"t1"|"t2"|"t3"`) or `"ok"`.
  - `heuristic_decision() -> GateDecision` — a convenience returning `GateDecision(True, 1.0, "heuristic")` for the current recognizer path.

**Invariant:** callers must not dispatch when `decision.fire is False`. Task 6/7 integration test enforces "abstain ⇒ no dispatch".

- [ ] **Step 1: Write failing tests** — truth table over T1/T2/T3 (each gate independently blocks; all-pass fires; reason string correct); permissive defaults always fire; `heuristic_decision().fire is True`.
- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement `gate.py`.**
- [ ] **Step 4: Run, verify pass.**
- [ ] **Step 5: Commit** — `git commit -m "feat(gate): confidence-gate abstention seam"`

---

### Task 6: IPC boundary (`ipc.py`)

**Files:**
- Create: `src/aircontrol/ipc.py`
- Test: `tests/test_ipc.py`
- Modify: `pyproject.toml` (add `websockets`)

**Interfaces:**
- Produces:
  - Message builders/validators for the v1 schema (see spec §5): `status_event(...)`, `action_event(...)`, `candidate_event(...)`, `metrics_event(...)`, `parse_command(raw: str) -> dict` (raises `IpcProtocolError` on bad version/shape).
  - `IpcServer(host="127.0.0.1", port=8787)` with `start()`, `stop()`, `broadcast(event: dict)`, and `on_command(callback: Callable[[dict], None])`. Runs its own asyncio loop on a background thread; binds loopback only.
  - `IpcClient(url)` async client with `send_command(name)` and an async iterator of events — used by tests and the future UI.
  - `FakeTransport` — in-process pub/sub implementing the same `broadcast`/`on_command` surface for unit tests without sockets.
  - `IpcProtocolError(Exception)`.
- Every message includes `"v": 1`. Unknown `type` on inbound → logged + ignored; wrong `v` → `IpcProtocolError`.

- [ ] **Step 1: Write failing tests** — builders produce dicts with `v==1` and required keys; `parse_command` accepts valid, raises on wrong version/missing name; `FakeTransport` delivers broadcasts to subscribers and routes commands to the callback; a real-socket round-trip test (start server on `127.0.0.1:0`, connect `IpcClient`, send a command, receive a broadcast) guarded to skip if `websockets` missing.
- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement `ipc.py`; add `websockets` to `pyproject` deps; `pip install -q websockets` into `env/`.**
- [ ] **Step 4: Run, verify pass.**
- [ ] **Step 5: Commit** — `git commit -m "feat(ipc): loopback WebSocket boundary + schema + fake transport"`

---

### Task 7: Pipeline + daemon assembly, port, and integration

**Files:**
- Create: `src/aircontrol/pipeline.py`
- Create: `src/aircontrol/daemon.py`
- Modify: `src/aircontrol/config.py` (new config sections)
- Modify: `src/aircontrol/app.py` (feed observations to the pipeline; keep HUD)
- Test: `tests/test_pipeline_integration.py`

**Interfaces:**
- `config.py` adds (with defaults + validation, merged into `AppConfig`):
  - `PipelineConfig(resample_length: int = 45, buffer_capacity: int = 75)`
  - `StoreConfig(db_path: str = "")` (empty → per-user app-data dir resolved at runtime)
  - `MetricsConfig(cpu_sampling: bool = True)`
  - `IpcConfig(enabled: bool = False, host: str = "127.0.0.1", port: int = 8787)`
- `pipeline.py`:
  - `Pipeline(config: AppConfig, controller: ActionController, *, store: Store | None, metrics: Metrics, gate: ConfidenceGate, clock)`.
  - `.process(observation: HandObservation | None, now: float) -> list[PipelineEvent]` — runs recognizer → append to `RollingFrameBuffer` (when observation present) → `engine.update` → for each action, consult gate (`heuristic_decision()` this spec) and **only dispatch when `fire`** → record metrics → return events (status/action/candidate) mirroring the IPC schema dicts.
  - `.status() -> dict` (status event). `.toggle_arm(now)`, `.force_pause(reason)`, `.release()`.
- `daemon.py`:
  - `Daemon(config, *, practice: bool, controller, store, ipc=None)` with `.feed(observation, now)`, `.command(name)`, `.start()`, `.stop()`; wires `Pipeline`; if `ipc` provided, broadcasts events and registers `command` as the command callback.

**Port:** `app.py`'s loop keeps camera + HUD, but replaces the inline `recognizer.recognize` + `engine.update` + `controller.dispatch_all` block with `daemon.feed(observation, now)` and renders from `daemon.status()`/returned events. Behavior must match today's app. The socket server stays **off** by default (`IpcConfig.enabled = False`); the HUD path is embedded/in-process.

- [ ] **Step 1: Write failing integration test** driving the pipeline with scripted synthetic `HandObservation`s (no camera), using a `DryRunInputSink`-backed `ActionController` and an in-memory `Store`:
  - arming sequence (open-palm hold) then a pointer motion dispatches a `MOVE_POINTER`;
  - a gate with blocking thresholds injected → **no** dispatch (abstain invariant);
  - buffer length grows with observations and is bounded by `buffer_capacity`;
  - emitted events match the IPC schema shape (`v==1`, required keys).

```python
# tests/test_pipeline_integration.py (sketch of the abstain invariant)
def test_blocking_gate_suppresses_dispatch(scripted_pointer_obs, in_memory_store):
    from aircontrol.gate import ConfidenceGate, GateThresholds
    gate = ConfidenceGate(GateThresholds(t1_top1=2.0))  # impossible → always abstain
    pipeline = make_pipeline(gate=gate, store=in_memory_store)
    for obs, t in scripted_pointer_obs:
        pipeline.process(obs, t)
    assert pipeline.controller.sink.events == []  # nothing dispatched
```

- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement** `PipelineConfig`/`StoreConfig`/`MetricsConfig`/`IpcConfig` + validation; `pipeline.py`; `daemon.py`; port `app.py`.
- [ ] **Step 4: Run full suite** — `python -m pytest -p no:cacheprovider` → all pass (61 existing + new). Confirm `python -m aircontrol --practice --help`-style import path is intact (import `aircontrol.app` without a camera must not crash at import).
- [ ] **Step 5: Commit** — `git commit -m "feat(daemon): pipeline + daemon assembly; port app loop behind seams"`

---

## Self-Review

**Spec coverage:** §3.1 trajectory→Task 1; §3.2 buffer→Task 2; §3.3 store→Task 3; §3.5 metrics→Task 4; §3.6 gate→Task 5; §3.4 ipc→Task 6; §3.4 daemon + §3.7 port + config→Task 7; §5 schema→Tasks 6–7; §7 testing→every task + integration; §12 privacy invariants→Tasks 1 (no-pixel), 3 (delete_everything), 6 (loopback). Covered.

**Placeholder scan:** no TBD/TODO; each task has concrete signatures, DDL, algorithm, and test code. Implementation bodies for store/ipc/daemon are pinned by explicit interfaces + tests (Codex writes bodies to pass them).

**Type consistency:** `Trajectory`/`LandmarkFrame`/`NormalizedTrajectory` names consistent across Tasks 1–3, 7; `GateDecision.fire` used identically in Tasks 5 and 7; `serialize/deserialize` signatures match between Tasks 1 and 3; IPC `v:1` schema consistent between Task 6 and spec §5 and Task 7 events.
