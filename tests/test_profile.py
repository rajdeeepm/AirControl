from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from aircontrol.profile import (
    CalibrationProfile,
    InteractionVolume,
    LightingProfile,
    MotionSignature,
    from_payload,
    load_active_profile,
    save_profile,
    to_payload,
)
from aircontrol.store import Store


def make_profile() -> CalibrationProfile:
    return CalibrationProfile(
        hand_size=0.23,
        volume=InteractionVolume(
            x_min=0.1,
            x_max=0.9,
            y_min=0.2,
            y_max=0.8,
        ),
        motion=MotionSignature(
            velocity_floor=0.35,
            velocity_ceiling=2.4,
        ),
        lighting=LightingProfile(
            mean_brightness=128.5,
            landmark_jitter=0.012,
            acceptable=True,
        ),
        incidental_features=(
            (0.1, 0.2, 0.3),
            (1.1, 1.2, 1.3),
        ),
        created_at=1_720_000_000.25,
    )


@pytest.mark.parametrize(
    ("velocity_floor", "velocity_ceiling"),
    [
        (0.0, 1.0),
        (-0.1, 1.0),
        (1.0, 1.0),
        (2.0, 1.0),
    ],
)
def test_motion_signature_rejects_invalid_velocity_bounds(
    velocity_floor: float,
    velocity_ceiling: float,
) -> None:
    with pytest.raises(ValueError):
        MotionSignature(velocity_floor, velocity_ceiling)


@pytest.mark.parametrize("hand_size", [0.0, -0.01])
def test_calibration_profile_rejects_nonpositive_hand_size(
    hand_size: float,
) -> None:
    profile = make_profile()

    with pytest.raises(ValueError):
        CalibrationProfile(
            hand_size=hand_size,
            volume=profile.volume,
            motion=profile.motion,
            lighting=profile.lighting,
            incidental_features=profile.incidental_features,
            created_at=profile.created_at,
        )


@pytest.mark.parametrize(
    ("x", "y", "expected"),
    [
        (0.5, 0.5, True),
        (0.1, 0.2, True),
        (0.9, 0.8, True),
        (0.1, 0.8, True),
        (0.9, 0.2, True),
        (0.099, 0.5, False),
        (0.901, 0.5, False),
        (0.5, 0.199, False),
        (0.5, 0.801, False),
    ],
)
def test_interaction_volume_contains_uses_inclusive_bounds(
    x: float,
    y: float,
    expected: bool,
) -> None:
    volume = InteractionVolume(x_min=0.1, x_max=0.9, y_min=0.2, y_max=0.8)

    assert volume.contains(x, y) is expected


def test_profile_value_types_are_frozen_and_slotted() -> None:
    profile = make_profile()
    values = (profile.volume, profile.motion, profile.lighting, profile)

    for value in values:
        assert hasattr(type(value), "__slots__")
        assert not hasattr(value, "__dict__")
        with pytest.raises(FrozenInstanceError):
            setattr(value, next(iter(type(value).__dataclass_fields__)), 0.0)


def test_payload_round_trip_is_exact() -> None:
    profile = make_profile()

    payload = to_payload(profile)

    assert payload == {
        "v": 1,
        "hand_size": 0.23,
        "volume": {
            "x_min": 0.1,
            "x_max": 0.9,
            "y_min": 0.2,
            "y_max": 0.8,
        },
        "motion": {
            "velocity_floor": 0.35,
            "velocity_ceiling": 2.4,
        },
        "lighting": {
            "mean_brightness": 128.5,
            "landmark_jitter": 0.012,
            "acceptable": True,
        },
        "incidental_features": [
            [0.1, 0.2, 0.3],
            [1.1, 1.2, 1.3],
        ],
        "created_at": 1_720_000_000.25,
    }
    assert from_payload(payload) == profile


@pytest.mark.parametrize("version", [0, 2, "1", None])
def test_from_payload_rejects_wrong_version(version: object) -> None:
    payload = to_payload(make_profile())
    payload["v"] = version

    with pytest.raises(ValueError):
        from_payload(payload)


@pytest.mark.parametrize(
    "missing_key",
    [
        "v",
        "hand_size",
        "volume",
        "motion",
        "lighting",
        "incidental_features",
        "created_at",
    ],
)
def test_from_payload_rejects_missing_top_level_keys(missing_key: str) -> None:
    payload = to_payload(make_profile())
    del payload[missing_key]

    with pytest.raises(ValueError):
        from_payload(payload)


@pytest.mark.parametrize(
    ("section", "missing_key"),
    [
        ("volume", "x_min"),
        ("volume", "x_max"),
        ("volume", "y_min"),
        ("volume", "y_max"),
        ("motion", "velocity_floor"),
        ("motion", "velocity_ceiling"),
        ("lighting", "mean_brightness"),
        ("lighting", "landmark_jitter"),
        ("lighting", "acceptable"),
    ],
)
def test_from_payload_rejects_missing_nested_keys(
    section: str,
    missing_key: str,
) -> None:
    payload = to_payload(make_profile())
    section_payload = payload[section]
    assert isinstance(section_payload, dict)
    del section_payload[missing_key]

    with pytest.raises(ValueError):
        from_payload(payload)


def test_save_and_load_active_profile_through_store() -> None:
    profile = make_profile()

    with Store(":memory:") as store:
        result = save_profile(store, profile, name="desk")

        assert result is None
        active = store.calibration.active()
        assert active is not None
        assert active.name == "desk"
        assert active.active is True
        assert load_active_profile(store) == profile


def test_load_active_profile_returns_none_on_fresh_store() -> None:
    with Store(":memory:") as store:
        assert load_active_profile(store) is None
