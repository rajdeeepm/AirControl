from __future__ import annotations

import math
from dataclasses import FrozenInstanceError

import pytest

from aircontrol.domain import Point3D
from aircontrol.matcher import DtwMatcher, MatchResult
from aircontrol.store import Store
from aircontrol.trajectory import LandmarkFrame, Trajectory


_ANCHOR_INDICES = frozenset((0, 5, 9, 17))
_FRAME_COUNT = 18
_MOTION_KINDS = ("horizontal", "vertical", "circle")


def _motion_frame(
    x_offset: float,
    y_offset: float,
    *,
    frame_index: int,
    noise: float,
    phase: float,
    timestamp: float,
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
            x=point.x + x_offset + noise * math.sin(angle),
            y=point.y + y_offset + noise * math.cos(angle),
            z=point.z,
        )

    return LandmarkFrame(
        landmarks=tuple(landmarks),
        handedness="Right",
        timestamp=timestamp,
    )


def _motion_trajectory(
    kind: str,
    *,
    amplitude: float = 1.0,
    timing_power: float = 1.0,
    noise: float = 0.0,
    phase: float = 0.0,
) -> Trajectory:
    frames = []
    for frame_index in range(_FRAME_COUNT):
        progress = frame_index / (_FRAME_COUNT - 1)
        if kind == "horizontal":
            x_offset, y_offset = amplitude * progress, 0.0
        elif kind == "vertical":
            x_offset, y_offset = 0.0, amplitude * progress
        elif kind == "circle":
            angle = 2.0 * math.pi * progress
            x_offset = amplitude * (math.cos(angle) - 1.0)
            y_offset = amplitude * math.sin(angle)
        else:
            raise ValueError(f"Unknown motion kind: {kind}")

        frames.append(
            _motion_frame(
                x_offset,
                y_offset,
                frame_index=frame_index,
                noise=noise,
                phase=phase,
                timestamp=progress**timing_power,
            )
        )
    return Trajectory(frames=tuple(frames), handedness="Right")


def _seed_three_classes(store: Store) -> dict[str, int]:
    gesture_ids = {}
    for kind in _MOTION_KINDS:
        gesture = store.gestures.add(kind.title())
        gesture_ids[kind] = gesture.id
        for exemplar_index in range(8):
            centered_index = exemplar_index - 3.5
            store.exemplars.add(
                gesture.id,
                _motion_trajectory(
                    kind,
                    amplitude=1.0 + centered_index * 0.025,
                    timing_power=1.0 + centered_index * 0.035,
                    noise=0.004 + exemplar_index * 0.001,
                    phase=exemplar_index * 0.41,
                ),
            )
    return gesture_ids


def test_empty_library_returns_empty_match_result() -> None:
    with Store(":memory:") as store:
        matcher = DtwMatcher(store)
        matcher.refresh()

        result = matcher.match(_motion_trajectory("horizontal"))

    assert result == MatchResult(None, 0.0, 0.0, {})


def test_single_gesture_library_has_zero_top2() -> None:
    with Store(":memory:") as store:
        gesture = store.gestures.add("Horizontal")
        store.exemplars.add(gesture.id, _motion_trajectory("horizontal"))
        matcher = DtwMatcher(store)
        matcher.refresh()

        result = matcher.match(
            _motion_trajectory(
                "horizontal",
                amplitude=1.04,
                timing_power=1.08,
                noise=0.006,
                phase=0.3,
            )
        )

    assert result.gesture_id == gesture.id
    assert result.top1 > 0.0
    assert result.top2 == 0.0


def test_held_out_samples_match_three_jittered_gesture_classes() -> None:
    held_out = {
        "horizontal": _motion_trajectory(
            "horizontal",
            amplitude=1.03,
            timing_power=1.08,
            noise=0.009,
            phase=0.23,
        ),
        "vertical": _motion_trajectory(
            "vertical",
            amplitude=0.97,
            timing_power=0.92,
            noise=0.010,
            phase=0.67,
        ),
        "circle": _motion_trajectory(
            "circle",
            amplitude=1.05,
            timing_power=1.04,
            noise=0.008,
            phase=1.11,
        ),
    }

    with Store(":memory:") as store:
        gesture_ids = _seed_three_classes(store)
        matcher = DtwMatcher(store)
        matcher.refresh()

        for kind, trajectory in held_out.items():
            result = matcher.match(trajectory)

            assert result.gesture_id == gesture_ids[kind]
            assert result.top1 > result.top2 + 0.05


def test_refresh_picks_up_gesture_added_after_construction() -> None:
    with Store(":memory:") as store:
        horizontal = store.gestures.add("Horizontal")
        store.exemplars.add(horizontal.id, _motion_trajectory("horizontal"))
        matcher = DtwMatcher(store)
        matcher.refresh()

        vertical = store.gestures.add("Vertical")
        store.exemplars.add(vertical.id, _motion_trajectory("vertical"))
        assert set(matcher.match(_motion_trajectory("vertical")).scores) == {
            horizontal.id
        }

        matcher.refresh()
        result = matcher.match(_motion_trajectory("vertical"))

    assert vertical.id in result.scores
    assert result.gesture_id == vertical.id


def test_scores_contains_every_gesture_id() -> None:
    with Store(":memory:") as store:
        gesture_ids = _seed_three_classes(store)
        matcher = DtwMatcher(store)
        matcher.refresh()

        result = matcher.match(_motion_trajectory("horizontal", phase=0.19))

    assert set(result.scores) == set(gesture_ids.values())


def test_match_result_is_frozen_and_slotted() -> None:
    result = MatchResult(gesture_id=1, top1=0.9, top2=0.4, scores={1: 0.9})

    assert hasattr(MatchResult, "__slots__")
    assert not hasattr(result, "__dict__")
    with pytest.raises(FrozenInstanceError):
        result.top1 = 0.8
