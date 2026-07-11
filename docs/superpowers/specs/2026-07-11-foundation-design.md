# AirControl — Spec 1: Foundation

**Date:** 2026-07-11
**Status:** Approved for planning
**Owner:** Rajdeep Mukherjee (planning/orchestration: Claude; implementation: Codex)

---

## 0. Context

AirControl turns a laptop webcam into an on-device gesture-control modality for
the desktop. The full product vision lives in the build-direction document
(§1–§16), aimed first at RSI/carpal-tunnel users, engineered to two numbers:
**near-zero false positives per hour** and **high recall on deliberate
gestures**. All processing is on-device; **no camera frames are ever written to
disk** — only hand-landmark skeleton coordinates.

The existing codebase (`src/aircontrol/`) is a working, tested, Windows-first,
single-process heuristic MVP: MediaPipe capture (`vision.py`, `tracker.py`),
joint-angle pose recognition (`recognizer.py`), a temporal gesture state machine
(`engine.py`), Win32 `SendInput` dispatch (`input_sink.py`, `controller.py`), an
OpenCV HUD (`overlay.py`), and a metrics-consent gate (`privacy.py`). 61 unit
tests pass.

The 20-week vision is decomposed into five phase-aligned specs, each with its own
brainstorm → spec → plan → build → verify cycle:

| # | Spec | Maps to | Value |
|---|------|---------|-------|
| **1** | **Foundation (this doc)** | Phase 0→1 spine | Trajectory substrate, daemon boundary, persistence, metrics |
| 2 | Calibration & reliability core | Phase 1 | Wizard incl. negative-class capture, confidence gate, clutch options, undo |
| 3 | Embedding model | Phase 2 | Dataset extraction, DTW baseline first, encoder, ONNX, matcher |
| 4 | Custom gestures + practice arena | Phase 3 | Recording, confusability checks, arena, on-device learning |
| 5 | App UI + mapping | Phase 4 | Tauri/React onboarding, gesture library, mapping editor |

This spec covers **only Spec 1**.

---

## 1. Goal & Non-Goals

### Goal
Lay the architectural and data spine the rest of the product depends on, while
keeping a **runnable, all-green application at every step**. Specifically:

1. A privacy-verifiable **landmark-trajectory representation + normalization
   layer** (build-direction §3) that ML and storage both build on.
2. A **rolling trajectory buffer** in the capture path.
3. **SQLite persistence** for gestures, exemplar trajectories, mappings, and
   calibration profiles (build-direction §2 storage) — never frames.
4. A **two-process boundary**: an inference **daemon** exposing a status/event
   stream + command channel over a documented localhost IPC, with the existing
   single-process entry point preserved by embedding the daemon.
5. A **metrics harness** making the build-direction §13 targets measurable from
   day one.
6. A **confidence-gate seam** (the T1/T2/T3 abstention interface) that the
   Phase-3 matcher can drop into without touching the loop.
7. The existing recognizer/engine **ported behind these interfaces**, tests
   staying green, app still running.

### Non-Goals (explicitly deferred to later specs)
- Calibration wizard, negative-class capture, real confidence thresholds (Spec 2)
- Alternative clutch mechanisms, undo window (Spec 2)
- Embedding model, DTW baseline, dataset pipeline, matcher (Spec 3)
- Custom-gesture recording, confusability checks, practice arena (Spec 4)
- Any GUI beyond the existing OpenCV HUD; Tauri/React (Spec 5)
- macOS/Linux dispatch backends (dispatch stays Windows-only this spec)
- Network features of any kind beyond localhost IPC

---

## 2. Architecture

Target shape after this spec (new modules in **bold**):

```
                        ┌──────────────────────── daemon process ────────────────────────┐
Camera (30fps)          │                                                                 │
  └─► vision.py ─► tracker.py ─► recognizer.py ─► **trajectory.py** ─► engine.py           │
        (frames,          (21 landmarks)   (pose +        (normalize +      (state machine) │
         never stored)                      sample)        rolling buffer)        │          │
                        │                                        │                ▼          │
                        │                                  **gate.py** ──► controller.py ──► input_sink.py
                        │                                 (abstention seam)   (dispatch)   (Win32 SendInput)
                        │                                        │                          │
                        │   **metrics.py** (candidates/hr, latency, CPU, FP hook) ◄─────────┤
                        │   **store.py**  (SQLite: gestures, exemplars, mappings, calib)     │
                        │                                                                    │
                        │            **daemon.py** run loop  ──► **ipc.py** (WebSocket server)│
                        └──────────────────────────────────────────────┬────────────────────┘
                                                                        │ localhost JSON
                                                       (future) Tauri/React UI client  [Spec 5]
```

Two-process design: the **daemon** owns camera, tracking, recognition,
normalization, gating, dispatch, persistence, and metrics. The future UI is a
separate process speaking JSON over a localhost WebSocket. This spec builds the
daemon and the IPC boundary; the only IPC client for now is a **test/CLI client**.

The existing `__main__`/`app.py` entry point keeps working by **embedding** the
daemon in-process (no socket required for the local HUD path), so we never lose a
runnable app.

---

## 3. Components (each independently testable)

### 3.1 `trajectory.py` — representation & normalization (pure, no camera)
- **Types:** `LandmarkFrame` (21 × `(x,y,z)` = 63 floats + `handedness: str` +
  `timestamp: float`); `Trajectory` (ordered `tuple[LandmarkFrame, ...]` + a
  `handedness` flag + derived metadata).
- **Normalization pipeline** (build-direction §3), each step a pure function:
  1. Translate so wrist (landmark 0) is the origin.
  2. Scale by calibrated hand size (wrist→middle-MCP, landmark 0→9 distance) so
     hand size is factored out. Hand-size constant is a parameter (calibration
     supplies it in Spec 2; a per-trajectory fallback is used until then).
  3. Rotate to a canonical palm orientation for pose features, **keeping raw
     orientation as a parallel channel** (some gestures are orientation changes).
  4. Resample time to a fixed length (default **45 frames**) via linear
     interpolation; **keep raw timing as velocity features**.
- **Output:** a `NormalizedTrajectory` exposing the fixed-length canonical tensor
  + the raw-orientation channel + per-frame velocity features, ready for the
  future encoder. Uses `numpy`.
- **Serialization:** to/from compact `float32` arrays for storage (a 2-second
  gesture ≈ 8 KB). Round-trip must be exact within float32.
- **Privacy invariant (test-enforced):** trajectory types carry no pixel data;
  there is no code path from a frame buffer into a `Trajectory`.

### 3.2 Rolling trajectory buffer
- A bounded ring buffer of recent `LandmarkFrame`s (default capacity covers the
  max gesture duration ~2.5 s at 30 fps ≈ 75 frames, configurable).
- Lives in the capture path; fed from `tracker.py` output. Exposes "last N
  frames" / "since timestamp" slices for the (future) segmentation + encoder.
- The existing frame-by-frame heuristic recognizer continues to run unchanged in
  parallel — the buffer is additive this spec.

### 3.3 `store.py` — SQLite persistence
- Single local DB file (path configurable; default under a per-user app-data dir,
  **not** the repo). WAL mode.
- **Schema (v1), with migration scaffolding:**
  - `schema_version` (single-row version marker for migrations).
  - `gestures` (id, name, description, created_at, updated_at).
  - `exemplars` (id, gesture_id FK, trajectory BLOB float32, frame_count,
    created_at) — landmark trajectories only, **never frames**.
  - `mappings` (id, gesture_id FK, **context** TEXT default `'global'`, action
    JSON, enabled, created_at) — modeled as `gesture × context → action` so
    Spec-2/5 per-app profiles are a UI change, **not** a migration.
  - `calibration_profiles` (id, name, payload JSON — hand size, interaction
    volume, motion signature, lighting profile, active flag). Written by Spec 2;
    the table + accessors exist now.
- **API:** typed CRUD repository classes; migrations idempotent and tested; a
  documented "delete everything" operation (build-direction §12) that truly
  clears the store.
- Pure stdlib `sqlite3`; no ORM.

### 3.4 `daemon.py` + `ipc.py` — process boundary
- `daemon.py`: extracts the run loop into a `Daemon` object with explicit
  lifecycle (`start`/`stop`), owning vision → tracker → recognizer → trajectory
  → engine → gate → controller, plus metrics + store. Emits **status/event**
  objects (armed state, last action + confidence, candidate events, metrics
  snapshots) and accepts **commands** (arm/pause toggle, quit, get-status).
- `ipc.py`: a localhost **WebSocket** server exposing:
  - an outbound **event stream** (JSON, documented message schema + version),
  - an inbound **command channel** (JSON).
  - A thin **client** class and an in-process **fake transport** for tests.
  - Bind to `127.0.0.1` only; no external interface. (Build-direction §12: the
    daemon has no outbound network use; the WebSocket is loopback-only.)
- **Message schema** documented in the module and mirrored in the spec's
  §5. Versioned so the Spec-5 UI can evolve independently.
- The local HUD path (`app.py`) embeds the `Daemon` directly (no socket needed);
  the socket server is opt-in via a flag for the future UI and for integration
  tests.
- **Dependency:** the `websockets` library (async, pure-Python). Added to
  `pyproject` deps.

### 3.5 `metrics.py` — instrumentation harness
- Counters/timers computing build-direction §13 signals **locally**:
  - candidates emitted per hour (unclutched vs armed),
  - false-positive log hook (an API the undo path in Spec 2 will call),
  - end-to-end latency: motion-offset → action dispatch,
  - CPU sampling (idle/armed) via `os`/`resource` or `psutil` if present
    (optional dep; degrade gracefully).
- **Export:** a local, user-inspectable JSON snapshot; **opt-in** and
  aggregate-only, readable before any (future) send. Nothing leaves the machine
  this spec.
- Wired into the daemon loop with negligible overhead; unit-tested with a fake
  clock.

### 3.6 `gate.py` — confidence-gate seam
- Defines the **abstention interface**: given a candidate + per-class
  similarities, apply the T1/T2/T3 decision rule (top-1 ≥ T1, margin over top-2 ≥
  T2, distance from incidental-motion distribution ≥ T3) → `Fire | Abstain` with
  a reason and a confidence value.
- This spec ships a **pass-through/heuristic implementation**: the existing
  recognizer supplies trivial high confidence so current behavior is unchanged;
  thresholds are config with permissive defaults. Real thresholds + density model
  arrive in Spec 2/3. The seam ensures the matcher drops in without touching the
  loop.
- **Policy invariant (test-enforced):** when the gate returns `Abstain`, **no
  action is dispatched**.

### 3.7 Porting existing engine/recognizer
- Introduce the interfaces above and route the current pipeline through them
  (`recognizer → trajectory buffer (additive) → engine → gate → controller`),
  changing behavior **as little as possible**.
- All 61 existing tests must remain green; changed call sites updated in place.

---

## 4. Data Flow

1. `vision.py` yields the latest frame (in-memory only; never persisted).
2. `tracker.py` produces a `HandObservation` (21 landmarks + world landmarks).
3. `recognizer.py` produces the current `GestureSample` (unchanged heuristic).
4. Each observation appends a `LandmarkFrame` to the **rolling buffer**;
   `trajectory.py` can normalize any slice on demand.
5. `engine.py` consumes samples and produces `Action`s (unchanged state machine).
6. `gate.py` wraps action emission: fire vs abstain (pass-through this spec).
7. `controller.py` dispatches fired actions via `input_sink.py`.
8. `metrics.py` observes candidates, latencies, and dispatches throughout.
9. `store.py` persists gestures/exemplars/mappings/calibration when written
   (write paths land in later specs; schema + API exist now).
10. `daemon.py` orchestrates 1–9 and emits events / accepts commands via
    `ipc.py` (or in-process for the HUD).

---

## 5. IPC Message Schema (v1)

All messages are JSON objects with a `type` and a top-level `v: 1`.

**Daemon → client (events):**
- `{"v":1,"type":"status","armed":bool,"raw_pose":str,"active_pose":str,"hold_progress":float,"hand_visible":bool,"status_text":str}`
- `{"v":1,"type":"action","kind":str,"confidence":float,"description":str,"ts":float}`
- `{"v":1,"type":"candidate","gate":"fire"|"abstain","reason":str,"confidence":float,"ts":float}`
- `{"v":1,"type":"metrics","candidates_per_hour":float,"fp_per_hour":float,"latency_ms_p50":float,"cpu_pct":float,"ts":float}`

**Client → daemon (commands):**
- `{"v":1,"type":"command","name":"toggle_arm"|"pause"|"quit"|"get_status"}`

Unknown message types are ignored with a logged warning; version mismatch is
rejected with an `error` event. Schema changes bump `v`.

---

## 6. Error Handling
- Camera/model stalls: existing watchdog behavior preserved; daemon surfaces a
  `status` event and pauses.
- IPC: malformed/oversized messages rejected without crashing the daemon; client
  disconnects are non-fatal; the loopback bind failure degrades to embedded-only
  mode with a clear log.
- Store: corrupt/locked DB is reported and does not take down the loop;
  migrations run in a transaction and roll back on failure.
- Held input is always released on daemon stop / disconnect / error (existing
  `release_all` guarantee preserved).

---

## 7. Testing Strategy

Verified in the Linux `env/` venv (WSL) via `pytest`; camera + real `SendInput`
smoke-tested by the user on Windows (`practice.cmd` / `start.cmd`).

- **`trajectory.py`:** synthetic hands — invariance checks (translation, scale,
  in-plane rotation), resample length/interpolation correctness, velocity-feature
  correctness, float32 round-trip, and the no-pixel-data privacy invariant.
- **Rolling buffer:** capacity, eviction, slice-by-count / slice-by-time.
- **`store.py`:** in-memory/temp-file DB — CRUD, FK integrity, migration from
  empty → v1, idempotent re-run, "delete everything" truly empties, mapping
  `context` defaulting to `global`.
- **`ipc.py`:** fake transport + real loopback socket — event serialization,
  command round-trip, version rejection, malformed-message resilience,
  loopback-only bind.
- **`metrics.py`:** fake clock — rate math, latency percentiles, FP hook,
  graceful degrade without `psutil`.
- **`gate.py`:** fire vs abstain truth table for T1/T2/T3; the "abstain ⇒ no
  dispatch" invariant.
- **Integration:** drive the daemon with a scripted sequence of synthetic
  `HandObservation`s (no camera) and assert the emitted event stream + dispatched
  actions match, end to end.
- **Regression:** all 61 existing tests stay green.

**Definition of done:** all above tests pass in WSL; `python -m aircontrol
--practice` still launches; the daemon runs headless against synthetic input; the
user confirms a Windows smoke test of live capture + dispatch is unchanged.

---

## 8. Risks & Mitigations
- **Refactor churn breaking the working app.** Mitigation: additive first
  (buffer, store, metrics as new modules), then thread interfaces through with
  the full suite as the gate; keep commits small and reviewable.
- **`websockets`/async creeping into the sync loop.** Mitigation: keep the daemon
  loop synchronous; run the WebSocket server on its own thread/loop behind
  `ipc.py`; the HUD path never touches it.
- **`opencv`/`mediapipe` unavailable in WSL.** Already handled: logic tests use
  headless `cv2`; mediapipe is lazy and mocked; camera tests are the user's
  Windows job.
- **DB path leaking into the repo or home dir.** Mitigation: default to a proper
  per-user app-data dir; tests use temp files; never write under version control.

---

## 9. Out-of-Scope Confirmations
This spec does **not** train models, add calibration, add custom gestures, add
any GUI framework, or change the gesture vocabulary. It is pure substrate + a
faithful port of current behavior behind new seams.
