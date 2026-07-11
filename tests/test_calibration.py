from __future__ import annotations

import json
import math

import pytest

from aircontrol.calibration import CalibrationRunner
from aircontrol.config import AppConfig, CalibrationConfig, load_config
from aircontrol.domain import HandObservation, Point3D


FINGER_LAYOUT = {
    "index": (5, 6, 7, 8, 0.43, 0.62),
    "middle": (9, 10, 11, 12, 0.49, 0.60),
    "ring": (13, 14, 15, 16, 0.55, 0.62),
    "pinky": (17, 18, 19, 20, 0.61, 0.66),
}


def make_hand(
    extended: tuple[str, ...] = (),
    *,
    dx: float = 0.0,
    dy: float = 0.0,
) -> HandObservation:
    points = [Point3D(0.5, 0.8) for _ in range(21)]
    points[0] = Point3D(0.5, 0.82)
    points[1] = Point3D(0.40, 0.72)
    points[2] = Point3D(0.34, 0.66)
    points[3] = Point3D(0.30, 0.60)
    points[4] = Point3D(0.27, 0.55)
    for name, (mcp, pip, dip, tip, x, y) in FINGER_LAYOUT.items():
        points[mcp] = Point3D(x, y)
        if name in extended:
            points[pip] = Point3D(x, y - 0.13)
            points[dip] = Point3D(x, y - 0.24)
            points[tip] = Point3D(x, y - 0.34)
        else:
            points[pip] = Point3D(x, y - 0.07)
            points[dip] = Point3D(x + 0.035, y - 0.01)
            points[tip] = Point3D(x + 0.018, y + 0.045)
    landmarks = tuple(
        Point3D(point.x + dx, point.y + dy, point.z) for point in points
    )
    return HandObservation(
        landmarks=landmarks,
        handedness="Left",
        confidence=0.99,
        image_width=1,
        image_height=1,
    )


class FakeClock:
    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def test_scripted_walkthrough_builds_a_valid_profile() -> None:
    config = AppConfig.defaults()
    config.calibration = CalibrationConfig(
        negative_seconds=10.0,
        snapshot_seconds=0.2,
        motion_reps=2,
    )
    clock = FakeClock(1234.5)
    runner = CalibrationRunner(config, clock=clock)
    open_palm = ("index", "middle", "ring", "pinky")

    framing_offsets = ((-0.15, -0.1), (0.15, -0.1), (0.15, 0.1), (-0.15, 0.1))
    for index, (dx, dy) in enumerate(framing_offsets):
        runner.feed(make_hand(open_palm, dx=dx, dy=dy), 120.0, index * 0.1)
    runner.advance()
    assert runner.current_step().name == "hand_snapshot"

    runner.feed(make_hand(open_palm), 120.0, 1.0)
    runner.feed(make_hand(open_palm), 121.0, 1.1)
    runner.feed(make_hand(open_palm), 119.0, 1.21)
    assert runner.current_step().name == "motion_signature"

    for now, dx in ((2.0, 0.0), (2.1, 0.02), (2.2, 0.04)):
        runner.feed(make_hand(open_palm, dx=dx), 120.0, now)
    runner.advance()
    assert runner.current_step().name == "motion_signature"
    assert runner.current_step().progress == pytest.approx(0.5)

    for now, dx in ((3.0, 0.0), (3.1, 0.06), (3.2, 0.12)):
        runner.feed(make_hand(open_palm, dx=dx), 120.0, now)
    runner.advance()
    assert runner.current_step().name == "negative_capture"

    for index in range(21):
        now = 4.0 + index * 0.5
        runner.feed(
            make_hand(("index",), dx=0.003 * math.sin(index)),
            120.0,
            now,
        )
    assert runner.current_step().name == "lighting"

    for index, dx in enumerate((0.0, 0.001, -0.001, 0.001, 0.0)):
        runner.feed(make_hand(open_palm, dx=dx), 120.0, 15.0 + index * 0.1)
    runner.advance()

    assert runner.is_complete()
    profile = runner.result()
    base_center = (0.516, 0.664)
    for dx, dy in framing_offsets:
        assert profile.volume.contains(base_center[0] + dx, base_center[1] + dy)
    assert profile.hand_size == pytest.approx(math.hypot(0.01, 0.22))
    assert 0.0 < profile.motion.velocity_floor < profile.motion.velocity_ceiling
    assert len(profile.incidental_features) >= 5
    assert profile.lighting.mean_brightness == pytest.approx(120.0)
    assert profile.lighting.landmark_jitter < 0.05
    assert profile.lighting.acceptable
    assert profile.created_at == 1234.5


def test_missing_data_cannot_be_advanced_and_result_raises_early() -> None:
    runner = CalibrationRunner(AppConfig.defaults())

    for _ in range(10):
        runner.advance()

    assert runner.current_step().name == "framing"
    assert not runner.is_complete()
    with pytest.raises(RuntimeError, match="complete"):
        runner.result()


def test_snapshot_needs_multiple_open_palm_frames() -> None:
    config = AppConfig.defaults()
    config.calibration.snapshot_seconds = 0.1
    runner = CalibrationRunner(config)
    open_palm = ("index", "middle", "ring", "pinky")

    runner.feed(make_hand(open_palm, dx=-0.1, dy=-0.1), 120.0, 0.0)
    runner.feed(make_hand(open_palm, dx=0.1, dy=0.1), 120.0, 0.1)
    runner.advance()
    runner.feed(make_hand(open_palm), 120.0, 1.0)
    runner.feed(None, 120.0, 1.11)

    assert runner.current_step().name == "hand_snapshot"

    runner.feed(make_hand(open_palm), 120.0, 1.12)
    assert runner.current_step().name == "motion_signature"


@pytest.mark.parametrize(
    "calibration",
    [
        {"negative_seconds": 0.0},
        {"negative_seconds": -1.0},
        {"snapshot_seconds": 0.0},
        {"snapshot_seconds": -1.0},
        {"snapshot_seconds": float("nan")},
        {"snapshot_seconds": float("inf")},
        {"motion_reps": 0},
        {"motion_reps": -1},
        {"motion_reps": 1.5},
        {"motion_reps": True},
    ],
)
def test_load_config_rejects_invalid_calibration_settings(
    tmp_path,
    calibration: dict[str, object],
) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"calibration": calibration}))

    with pytest.raises(ValueError):
        load_config(path)


def test_calibration_config_defaults_can_be_tuned_in_place() -> None:
    config = AppConfig.defaults()

    config.calibration.negative_seconds = 0.5
    config.calibration.snapshot_seconds = 0.1
    config.calibration.motion_reps = 1

    assert config.calibration == CalibrationConfig(0.5, 0.1, 1)


def test_short_negative_capture_auto_completes_with_one_feature_row() -> None:
    config = AppConfig.defaults()
    config.calibration = CalibrationConfig(
        negative_seconds=0.5,
        snapshot_seconds=0.1,
        motion_reps=1,
    )
    runner = CalibrationRunner(config)
    open_palm = ("index", "middle", "ring", "pinky")

    runner.feed(make_hand(open_palm, dx=-0.1, dy=-0.1), 120.0, 0.0)
    runner.feed(make_hand(open_palm, dx=0.1, dy=0.1), 120.0, 0.1)
    runner.advance()
    runner.feed(make_hand(open_palm), 120.0, 1.0)
    runner.feed(make_hand(open_palm), 120.0, 1.11)
    for now, dx in ((2.0, 0.0), (2.1, 0.02), (2.2, 0.04)):
        runner.feed(make_hand(open_palm, dx=dx), 120.0, now)
    runner.advance()

    runner.feed(make_hand(("index",)), 120.0, 3.0)
    runner.feed(make_hand(("index",), dx=0.001), 120.0, 3.5)

    assert runner.current_step().name == "lighting"
    runner.feed(make_hand(open_palm), 120.0, 4.0)
    runner.advance()
    assert runner.current_step().name == "lighting"
    runner.feed(make_hand(open_palm, dx=0.001), 121.0, 4.1)
    runner.advance()
    assert len(runner.result().incidental_features) == 1
