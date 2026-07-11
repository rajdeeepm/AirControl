from __future__ import annotations

from dataclasses import dataclass

from aircontrol.store import Store


_PAYLOAD_VERSION = 1


@dataclass(frozen=True, slots=True)
class InteractionVolume:
    x_min: float
    x_max: float
    y_min: float
    y_max: float

    def contains(self, x: float, y: float) -> bool:
        return self.x_min <= x <= self.x_max and self.y_min <= y <= self.y_max


@dataclass(frozen=True, slots=True)
class MotionSignature:
    velocity_floor: float
    velocity_ceiling: float

    def __post_init__(self) -> None:
        if not 0 < self.velocity_floor < self.velocity_ceiling:
            raise ValueError("velocity bounds must satisfy 0 < floor < ceiling")


@dataclass(frozen=True, slots=True)
class LightingProfile:
    mean_brightness: float
    landmark_jitter: float
    acceptable: bool


@dataclass(frozen=True, slots=True)
class CalibrationProfile:
    hand_size: float
    volume: InteractionVolume
    motion: MotionSignature
    lighting: LightingProfile
    incidental_features: tuple[tuple[float, ...], ...]
    created_at: float

    def __post_init__(self) -> None:
        if not self.hand_size > 0:
            raise ValueError("hand_size must be greater than zero")


def to_payload(profile: CalibrationProfile) -> dict[str, object]:
    return {
        "v": _PAYLOAD_VERSION,
        "hand_size": profile.hand_size,
        "volume": {
            "x_min": profile.volume.x_min,
            "x_max": profile.volume.x_max,
            "y_min": profile.volume.y_min,
            "y_max": profile.volume.y_max,
        },
        "motion": {
            "velocity_floor": profile.motion.velocity_floor,
            "velocity_ceiling": profile.motion.velocity_ceiling,
        },
        "lighting": {
            "mean_brightness": profile.lighting.mean_brightness,
            "landmark_jitter": profile.lighting.landmark_jitter,
            "acceptable": profile.lighting.acceptable,
        },
        "incidental_features": [list(row) for row in profile.incidental_features],
        "created_at": profile.created_at,
    }


def from_payload(payload: dict[str, object]) -> CalibrationProfile:
    if not isinstance(payload, dict):
        raise ValueError("calibration profile payload must be an object")

    version = payload.get("v")
    if type(version) is not int or version != _PAYLOAD_VERSION:
        raise ValueError(f"unsupported calibration profile version: {version!r}")

    volume_payload = _object(payload, "volume")
    motion_payload = _object(payload, "motion")
    lighting_payload = _object(payload, "lighting")
    feature_rows = _sequence(payload, "incidental_features")

    incidental_features: list[tuple[float, ...]] = []
    for index, row in enumerate(feature_rows):
        if not isinstance(row, (list, tuple)):
            raise ValueError(f"incidental_features[{index}] must be an array")
        incidental_features.append(
            tuple(
                _number(value, f"incidental_features[{index}][{column}]")
                for column, value in enumerate(row)
            )
        )

    return CalibrationProfile(
        hand_size=_number(_required(payload, "hand_size"), "hand_size"),
        volume=InteractionVolume(
            x_min=_number(_required(volume_payload, "x_min"), "volume.x_min"),
            x_max=_number(_required(volume_payload, "x_max"), "volume.x_max"),
            y_min=_number(_required(volume_payload, "y_min"), "volume.y_min"),
            y_max=_number(_required(volume_payload, "y_max"), "volume.y_max"),
        ),
        motion=MotionSignature(
            velocity_floor=_number(
                _required(motion_payload, "velocity_floor"),
                "motion.velocity_floor",
            ),
            velocity_ceiling=_number(
                _required(motion_payload, "velocity_ceiling"),
                "motion.velocity_ceiling",
            ),
        ),
        lighting=LightingProfile(
            mean_brightness=_number(
                _required(lighting_payload, "mean_brightness"),
                "lighting.mean_brightness",
            ),
            landmark_jitter=_number(
                _required(lighting_payload, "landmark_jitter"),
                "lighting.landmark_jitter",
            ),
            acceptable=_boolean(
                _required(lighting_payload, "acceptable"),
                "lighting.acceptable",
            ),
        ),
        incidental_features=tuple(incidental_features),
        created_at=_number(_required(payload, "created_at"), "created_at"),
    )


def save_profile(
    store: Store,
    profile: CalibrationProfile,
    name: str = "default",
) -> None:
    store.calibration.save(name, to_payload(profile), active=True)


def load_active_profile(store: Store) -> CalibrationProfile | None:
    record = store.calibration.active()
    if record is None:
        return None
    return from_payload(record.payload)


def _required(payload: dict[str, object], key: str) -> object:
    try:
        return payload[key]
    except KeyError as exc:
        raise ValueError(f"missing calibration profile key: {key}") from exc


def _object(payload: dict[str, object], key: str) -> dict[str, object]:
    value = _required(payload, key)
    if not isinstance(value, dict):
        raise ValueError(f"{key} must be an object")
    return value


def _sequence(payload: dict[str, object], key: str) -> list[object] | tuple[object, ...]:
    value = _required(payload, key)
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{key} must be an array")
    return value


def _number(value: object, key: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{key} must be a number")
    return float(value)


def _boolean(value: object, key: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be a boolean")
    return value
