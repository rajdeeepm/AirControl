from __future__ import annotations

import math
from dataclasses import FrozenInstanceError

import pytest

from aircontrol.arena import ArenaPrompt, ArenaSession, effective_t1
from aircontrol.domain import Point3D
from aircontrol.gate import GateThresholds
from aircontrol.matcher import MatchResult
from aircontrol.segmentation import CandidateSegment
from aircontrol.store import Store
from aircontrol.trajectory import LandmarkFrame, Trajectory, deserialize, serialize


_ANCHOR_INDICES = frozenset((0, 5, 9, 17))
_FRAME_COUNT = 8


def _motion_frame(
    x_offset: float,
    y_offset: float,
    *,
    frame_index: int,
    phase: float,
    timestamp: float,
    handedness: str,
) -> LandmarkFrame:
    landmarks = [
        Point3D(
            x=(index % 5) * 0.08,
            y=(index // 5) * 0.12,
            z=(index % 3) * 0.02,
        )
        for index in range(21)
    ]
    landmarks[0] = Point3D(x=0.0, y=0.0, z=0.0)
    landmarks[5] = Point3D(x=1.0, y=0.0, z=0.0)
    landmarks[9] = Point3D(x=0.0, y=1.0, z=0.0)
    landmarks[17] = Point3D(x=0.0, y=0.8, z=0.0)

    for index, point in enumerate(landmarks):
        if index in _ANCHOR_INDICES:
            continue
        angle = phase + frame_index * 0.73 + index * 0.19
        landmarks[index] = Point3D(
            x=point.x + x_offset + 0.004 * math.sin(angle),
            y=point.y + y_offset + 0.004 * math.cos(angle),
            z=point.z,
        )

    return LandmarkFrame(
        landmarks=tuple(landmarks),
        handedness=handedness,
        timestamp=timestamp,
    )


def _motion_trajectory(
    kind: str,
    *,
    amplitude: float = 1.0,
    phase: float = 0.0,
    handedness: str = "Right",
) -> Trajectory:
    frames = []
    for frame_index in range(_FRAME_COUNT):
        progress = frame_index / (_FRAME_COUNT - 1)
        if kind == "horizontal":
            x_offset, y_offset = amplitude * progress, 0.0
        elif kind == "vertical":
            x_offset, y_offset = 0.0, amplitude * progress
        else:
            raise ValueError(f"Unknown motion kind: {kind}")

        frames.append(
            _motion_frame(
                x_offset,
                y_offset,
                frame_index=frame_index,
                phase=phase,
                timestamp=progress,
                handedness=handedness,
            )
        )
    return Trajectory(frames=tuple(frames), handedness=handedness)


def _segment(trajectory: Trajectory | None = None) -> CandidateSegment:
    return CandidateSegment(
        trajectory=trajectory or _motion_trajectory("horizontal"),
        t_onset=0.0,
        t_offset=1.0,
    )


def _result(gesture_id: int, top1: float = 0.9) -> MatchResult:
    return MatchResult(
        gesture_id=gesture_id,
        top1=top1,
        top2=0.0,
        scores={gesture_id: top1},
    )


class _FakeClock:
    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def test_observe_returns_prompt_with_second_best_gesture_id() -> None:
    with Store(":memory:") as store:
        best = store.gestures.add("Best")
        runner_up = store.gestures.add("Runner up")
        third = store.gestures.add("Third")
        arena = ArenaSession(store)
        result = MatchResult(
            gesture_id=best.id,
            top1=0.91,
            top2=0.62,
            scores={third.id: 0.2, best.id: 0.91, runner_up.id: 0.62},
        )

        prompt = arena.observe(_segment(), result, fired=False)
        single_score_prompt = arena.observe(
            _segment(),
            _result(best.id),
            fired=False,
        )

    assert prompt == ArenaPrompt(best.id, 0.91, runner_up.id, 0.62)
    assert single_score_prompt.runner_up_id is None


def test_arena_prompt_is_frozen_and_slotted() -> None:
    prompt = ArenaPrompt(gesture_id=1, top1=0.9, runner_up_id=2, top2=0.4)

    assert hasattr(ArenaPrompt, "__slots__")
    assert not hasattr(prompt, "__dict__")
    with pytest.raises(FrozenInstanceError):
        prompt.top1 = 0.8


def test_confirm_adds_exemplar_and_records_confirm() -> None:
    with Store(":memory:") as store:
        gesture = store.gestures.add("Horizontal")
        arena = ArenaSession(store)

        arena.observe(_segment(), _result(gesture.id), fired=True)
        arena.confirm()
        arena.confirm()

        assert store.exemplars.count(gesture.id) == 1
        assert store.gesture_stats.get(gesture.id).confirms == 1


def test_confirm_prunes_to_cap_and_keeps_newest_exemplar() -> None:
    with Store(":memory:") as store:
        gesture = store.gestures.add("Horizontal")
        for index in range(15):
            store.exemplars.add(
                gesture.id,
                _motion_trajectory(
                    "horizontal",
                    amplitude=0.93 + index * 0.01,
                    phase=index * 0.31,
                ),
            )
        newest = _motion_trajectory("vertical", phase=0.77, handedness="Left")
        expected_newest = deserialize(serialize(newest), newest.handedness)
        arena = ArenaSession(store, max_exemplars=15)

        arena.observe(_segment(newest), _result(gesture.id), fired=False)
        arena.confirm()

        kept = store.exemplars.list(gesture.id)
        assert len(kept) == 15
        assert expected_newest in kept


def test_reject_records_reject_and_default_threshold_bump() -> None:
    with Store(":memory:") as store:
        gesture = store.gestures.add("Horizontal")
        arena = ArenaSession(store)

        arena.observe(_segment(), _result(gesture.id), fired=False)
        arena.reject()
        arena.reject()

        stats = store.gesture_stats.get(gesture.id)
        assert stats.rejects == 1
        assert stats.threshold_offset == pytest.approx(0.02)


def test_stress_counts_only_fired_observations_while_active() -> None:
    clock = _FakeClock()
    with Store(":memory:") as store:
        gesture = store.gestures.add("Horizontal")
        arena = ArenaSession(store, clock=clock)
        arena.start_stress(duration_seconds=5.0)

        assert arena.stress_active
        arena.observe(_segment(), _result(gesture.id), fired=True)
        arena.observe(_segment(), _result(gesture.id), fired=False)
        assert arena.stress_fires == 1

        clock.now = 5.1
        assert not arena.stress_active
        arena.observe(_segment(), _result(gesture.id), fired=True)

        assert arena.stress_fires == 1


def test_summary_includes_every_gesture_and_stress_fires() -> None:
    clock = _FakeClock()
    with Store(":memory:") as store:
        confirmed = store.gestures.add("Confirmed")
        rejected = store.gestures.add("Rejected")
        untouched = store.gestures.add("Untouched")
        arena = ArenaSession(store, clock=clock)

        arena.observe(_segment(), _result(confirmed.id), fired=False)
        arena.confirm()
        arena.observe(_segment(), _result(rejected.id), fired=False)
        arena.reject()
        arena.start_stress()
        arena.observe(_segment(), _result(confirmed.id), fired=True)

        summary = arena.summary()

    assert summary == {
        confirmed.id: {
            "confirms": 1,
            "rejects": 0,
            "threshold_offset": 0.0,
        },
        rejected.id: {
            "confirms": 0,
            "rejects": 1,
            "threshold_offset": pytest.approx(0.02),
        },
        untouched.id: {
            "confirms": 0,
            "rejects": 0,
            "threshold_offset": 0.0,
        },
        "stress_fires": 1,
    }


def test_confirm_and_reject_without_pending_are_safe_no_ops() -> None:
    with Store(":memory:") as store:
        gesture = store.gestures.add("Horizontal")
        arena = ArenaSession(store)

        arena.confirm()
        arena.reject()

        stats = store.gesture_stats.get(gesture.id)
        assert store.exemplars.count(gesture.id) == 0
        assert stats.confirms == 0
        assert stats.rejects == 0
        assert stats.threshold_offset == 0.0


def test_effective_t1_adds_offset_to_base_threshold() -> None:
    base = GateThresholds(t1_top1=0.71, t2_margin=0.12, t3_incidental=0.8)

    assert effective_t1(base, 0.07) == pytest.approx(0.78)
