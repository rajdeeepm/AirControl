from __future__ import annotations

import math
from dataclasses import FrozenInstanceError

import pytest

from aircontrol.domain import Point3D
from aircontrol.matcher import DtwMatcher, MatchResult
from aircontrol.pipeline import MIN_CUSTOM_CONFIDENCE
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


def _pose_frame(
    shape_offset: float,
    *,
    timestamp: float,
    handedness: str = "Right",
) -> LandmarkFrame:
    """A held, static hand shape: no motion, a fixed finger-curl pattern."""
    landmarks = [
        Point3D(
            x=(index % 5) * 0.08 + shape_offset * (index % 3),
            y=(index // 5) * 0.12,
            z=0.0,
        )
        for index in range(21)
    ]
    landmarks[0] = Point3D(x=0.0, y=0.0, z=0.0)
    landmarks[5] = Point3D(x=1.0, y=0.0, z=0.0)
    landmarks[9] = Point3D(x=0.0, y=1.0, z=0.0)
    landmarks[17] = Point3D(x=0.0, y=0.8, z=0.0)
    return LandmarkFrame(
        landmarks=tuple(landmarks),
        handedness=handedness,
        timestamp=timestamp,
    )


def _pose_trajectory(shape_offset: float, *, jitter: float = 0.0) -> Trajectory:
    frames = tuple(
        _pose_frame(shape_offset + jitter * math.sin(index), timestamp=index * 0.05)
        for index in range(6)
    )
    return Trajectory(frames=frames, handedness="Right")


def test_match_never_returns_a_pose_gesture() -> None:
    with Store(":memory:") as store:
        motion = store.gestures.add("Wave", kind="motion")
        store.exemplars.add(motion.id, _motion_trajectory("horizontal"))
        pose = store.gestures.add("Peace sign", kind="pose")
        store.exemplars.add(pose.id, _pose_trajectory(0.4))
        matcher = DtwMatcher(store)
        matcher.refresh()

        result = matcher.match(_motion_trajectory("horizontal", amplitude=1.02))

        assert result.gesture_id == motion.id
        assert pose.id not in result.scores


def test_match_pose_never_returns_a_motion_gesture() -> None:
    with Store(":memory:") as store:
        motion = store.gestures.add("Wave", kind="motion")
        store.exemplars.add(motion.id, _motion_trajectory("horizontal"))
        pose = store.gestures.add("Peace sign", kind="pose")
        store.exemplars.add(pose.id, _pose_trajectory(0.4))
        matcher = DtwMatcher(store)
        matcher.refresh()

        result = matcher.match_pose(_pose_trajectory(0.4, jitter=0.01))

        assert result.gesture_id == pose.id
        assert motion.id not in result.scores


def test_match_pose_prefers_the_closer_held_shape() -> None:
    with Store(":memory:") as store:
        peace = store.gestures.add("Peace sign", kind="pose")
        store.exemplars.add(peace.id, _pose_trajectory(0.1))
        fist_like = store.gestures.add("Custom fist-ish", kind="pose")
        store.exemplars.add(fist_like.id, _pose_trajectory(0.9))
        matcher = DtwMatcher(store)
        matcher.refresh()

        result = matcher.match_pose(_pose_trajectory(0.12, jitter=0.005))

        assert result.gesture_id == peace.id
        assert result.top1 > result.top2


# --- Discrimination: shape, spread/fold, and palm orientation ---------------
#
# Realistic hand shapes (not the crude _pose_frame offset above), used to
# prove the engineered feature vector actually separates a distinct finger
# shape and a flipped palm orientation from a genuine repeat -- and that a
# genuine repeat still clears the MIN_CUSTOM_CONFIDENCE similarity floor.

_FINGER_LAYOUT = {
    "index": (5, 6, 7, 8),
    "middle": (9, 10, 11, 12),
    "ring": (13, 14, 15, 16),
    "pinky": (17, 18, 19, 20),
}


def _base_hand_points() -> list[Point3D]:
    points = [Point3D(0.5, 0.8, 0.0) for _ in range(21)]
    points[0] = Point3D(0.5, 0.82, 0.0)
    points[1] = Point3D(0.40, 0.72, 0.0)
    points[2] = Point3D(0.34, 0.66, 0.0)
    points[3] = Point3D(0.30, 0.60, 0.0)
    points[4] = Point3D(0.27, 0.55, 0.0)
    return points


def _spock_hand(*, jitter: float = 0.0, seed: float = 0.0) -> tuple[Point3D, ...]:
    """Four fingers extended, parted between middle and ring."""
    spread_x = {"index": 0.43, "middle": 0.49, "ring": 0.70, "pinky": 0.76}
    y0 = {"index": 0.62, "middle": 0.60, "ring": 0.62, "pinky": 0.66}
    points = _base_hand_points()
    for name, (mcp, pip, dip, tip) in _FINGER_LAYOUT.items():
        x, y = spread_x[name], y0[name]
        points[mcp] = Point3D(x, y, 0.0)
        points[pip] = Point3D(x, y - 0.13, 0.0)
        points[dip] = Point3D(x, y - 0.24, 0.0)
        points[tip] = Point3D(x, y - 0.34, 0.0)
    return _jittered(points, jitter=jitter, seed=seed)


def _fist_hand(*, jitter: float = 0.0, seed: float = 0.0) -> tuple[Point3D, ...]:
    """Every finger curled tightly back toward the palm -- a clearly
    different shape from _spock_hand's spread, fully extended fingers."""
    x0 = {"index": 0.43, "middle": 0.49, "ring": 0.55, "pinky": 0.61}
    y0 = {"index": 0.62, "middle": 0.60, "ring": 0.62, "pinky": 0.66}
    points = _base_hand_points()
    for name, (mcp, pip, dip, tip) in _FINGER_LAYOUT.items():
        x, y = x0[name], y0[name]
        points[mcp] = Point3D(x, y, 0.0)
        points[pip] = Point3D(x, y - 0.07, 0.0)
        points[dip] = Point3D(x + 0.035, y - 0.01, 0.0)
        points[tip] = Point3D(x + 0.018, y + 0.045, 0.0)
    return _jittered(points, jitter=jitter, seed=seed)


def _mirror_x(points: tuple[Point3D, ...]) -> tuple[Point3D, ...]:
    """Flip palm-facing only: negating x for every landmark flips the sign
    of the signed cross product the palm-facing feature uses, while leaving
    every pairwise distance (spread/fold/thumb) exactly unchanged."""
    return tuple(Point3D(-point.x, point.y, point.z) for point in points)


def _jittered(
    points: list[Point3D],
    *,
    jitter: float,
    seed: float,
) -> tuple[Point3D, ...]:
    if jitter == 0.0:
        return tuple(points)
    return tuple(
        Point3D(
            point.x + jitter * math.sin(seed + index * 1.7),
            point.y + jitter * math.cos(seed + index * 2.3),
            point.z,
        )
        for index, point in enumerate(points)
    )


def _hand_trajectory(
    landmarks_fn,
    *,
    frame_count: int = 8,
    jitter: float = 0.0,
    mirror: bool = False,
) -> Trajectory:
    frames = []
    for index in range(frame_count):
        landmarks = landmarks_fn(jitter=jitter, seed=index * 0.9)
        if mirror:
            landmarks = _mirror_x(landmarks)
        frames.append(
            LandmarkFrame(landmarks=landmarks, handedness="Right", timestamp=index * 0.05)
        )
    return Trajectory(frames=tuple(frames), handedness="Right")


def _seed_spock_pose(store: Store) -> int:
    gesture = store.gestures.add("Spock", kind="pose")
    for _ in range(5):
        store.exemplars.add(
            gesture.id, _hand_trajectory(_spock_hand, jitter=0.004, frame_count=8)
        )
    return gesture.id


def test_correct_repeat_of_a_shape_clears_the_min_confidence_floor() -> None:
    with Store(":memory:") as store:
        _seed_spock_pose(store)
        matcher = DtwMatcher(store)
        matcher.refresh()

        result = matcher.match_pose(
            _hand_trajectory(_spock_hand, jitter=0.006, frame_count=8)
        )

        assert result.top1 >= MIN_CUSTOM_CONFIDENCE


def test_a_clearly_different_shape_scores_well_below_the_correct_match() -> None:
    with Store(":memory:") as store:
        _seed_spock_pose(store)
        matcher = DtwMatcher(store)
        matcher.refresh()

        correct = matcher.match_pose(
            _hand_trajectory(_spock_hand, jitter=0.006, frame_count=8)
        ).top1
        different = matcher.match_pose(
            _hand_trajectory(_fist_hand, jitter=0.006, frame_count=8)
        ).top1

        assert different < correct
        # Below the firing floor: it must not be able to cross-fire as Spock.
        assert different < MIN_CUSTOM_CONFIDENCE


def test_palm_toward_vs_away_now_produces_a_meaningfully_different_score() -> None:
    """Before engineered features, normalize()'s palm-basis rotation made
    matching orientation-invariant, so a mirrored (palm-away) performance of
    the *exact same finger shape* scored identically to the real repeat.
    With the palm-facing feature folded into the distance, it must not."""
    with Store(":memory:") as store:
        _seed_spock_pose(store)
        matcher = DtwMatcher(store)
        matcher.refresh()

        same_orientation = matcher.match_pose(
            _hand_trajectory(_spock_hand, jitter=0.004, frame_count=8)
        ).top1
        flipped_orientation = matcher.match_pose(
            _hand_trajectory(_spock_hand, jitter=0.004, frame_count=8, mirror=True)
        ).top1

        assert same_orientation - flipped_orientation > 0.2
        # This is the tightest real-world confusion case for
        # MIN_CUSTOM_CONFIDENCE (same shape, wrong orientation) -- it must
        # stay safely below the firing floor.
        assert flipped_orientation < MIN_CUSTOM_CONFIDENCE
