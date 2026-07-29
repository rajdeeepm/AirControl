from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from aircontrol.domain import HandObservation, Point3D


_LANDMARK_COUNT = 21
_COORDINATE_COUNT = _LANDMARK_COUNT * 3
_MINIMUM_NORM = 1e-9

# Engineered per-frame feature vector: [0] palm facing, [1:4] fingertip
# spread gaps, [4:8] fingertip-to-wrist folds, [8:10] thumb pose. See
# _engineered_features for the exact definition of each slot.
_FEATURE_COUNT = 10


@dataclass(frozen=True, slots=True)
class LandmarkFrame:
    landmarks: tuple[Point3D, ...]
    handedness: str
    timestamp: float

    def __post_init__(self) -> None:
        if len(self.landmarks) != _LANDMARK_COUNT:
            raise ValueError(
                f"Expected {_LANDMARK_COUNT} hand landmarks, received {len(self.landmarks)}"
            )


@dataclass(frozen=True, slots=True)
class Trajectory:
    frames: tuple[LandmarkFrame, ...]
    handedness: str


@dataclass(frozen=True, slots=True)
class NormalizedTrajectory:
    canonical: np.ndarray
    orientation: np.ndarray
    velocity: np.ndarray
    features: np.ndarray
    frame_count: int


def frame_from_observation(obs: HandObservation, timestamp: float) -> LandmarkFrame:
    return LandmarkFrame(
        landmarks=obs.landmarks,
        handedness=obs.handedness,
        timestamp=timestamp,
    )


def hand_size(frame: LandmarkFrame) -> float:
    wrist = frame.landmarks[0]
    middle_mcp = frame.landmarks[9]
    offset = np.array(
        [middle_mcp.x - wrist.x, middle_mcp.y - wrist.y, middle_mcp.z - wrist.z],
        dtype=np.float64,
    )
    return float(np.linalg.norm(offset))


def frame_shape(frame: LandmarkFrame) -> np.ndarray:
    """Wrist-centered, palm-size-normalized flattened shape of one frame.

    A cheap single-frame companion to :func:`normalize`: translation and
    scale invariant (like the full trajectory normalize), but skips the
    palm-basis rotation and resampling since callers only need a fast,
    comparable "shape fingerprint" -- pose capture stability checks and the
    live pose dwell/release logic, where cost matters more than the
    DTW-grade canonical basis a full trajectory match needs.
    """
    points = np.asarray(
        [[point.x, point.y, point.z] for point in frame.landmarks],
        dtype=np.float64,
    )
    wrist = points[0]
    scale = max(float(np.linalg.norm(points[9] - wrist)), _MINIMUM_NORM)
    return ((points - wrist) / scale).reshape(-1)


def shape_distance(a: np.ndarray, b: np.ndarray) -> float:
    """Mean absolute difference between two :func:`frame_shape` vectors."""
    return float(np.mean(np.abs(a - b)))


def _unit_vector(vector: np.ndarray, fallback: np.ndarray) -> np.ndarray:
    length = float(np.linalg.norm(vector))
    if length < _MINIMUM_NORM:
        return fallback.copy()
    return vector / length


def _perpendicular_unit(vector: np.ndarray) -> np.ndarray:
    axis = np.zeros(3, dtype=np.float64)
    axis[int(np.argmin(np.abs(vector)))] = 1.0
    perpendicular = np.cross(vector, axis)
    return _unit_vector(perpendicular, np.array([0.0, 1.0, 0.0]))


def _palm_basis(landmarks: np.ndarray) -> np.ndarray:
    default_x = np.array([1.0, 0.0, 0.0], dtype=np.float64)
    x_axis = _unit_vector(landmarks[5] - landmarks[0], default_x)
    tmp = landmarks[17] - landmarks[0]
    z_axis = _unit_vector(np.cross(x_axis, tmp), _perpendicular_unit(x_axis))
    y_axis = np.cross(z_axis, x_axis)
    return np.stack((x_axis, y_axis, z_axis))


def _engineered_features(landmarks: np.ndarray) -> np.ndarray:
    """Small per-frame feature vector distinguishing shapes that DTW-over-
    ``canonical`` alone confuses: palm facing, finger spread, finger fold,
    and thumb pose.

    ``landmarks`` must be the wrist-centered, hand-size-scaled array
    (translation/scale invariant like ``canonical``) but taken BEFORE the
    palm-basis rotation, so palm-facing survives -- the rotation that
    produces ``canonical`` is exactly what makes plain DTW orientation-
    invariant. Because ``landmarks`` is already divided by hand_size, every
    distance here comes out pre-normalized for free.

    Feature order:
      0   palm facing: signed 2D area of (5 - wrist) x (17 - wrist)
          (wrist is the origin post-centering). Sign flips between palm
          toward vs away from the camera; magnitude ~ palm_width^2.
      1:4 fingertip spread: dist(8, 12), dist(12, 16), dist(16, 20)
      4:8 finger fold: dist(0, 8), dist(0, 12), dist(0, 16), dist(0, 20)
      8:10 thumb pose: dist(4, 0), dist(4, 8)
    """
    features = np.empty((landmarks.shape[0], _FEATURE_COUNT), dtype=np.float64)

    index_mcp = landmarks[:, 5, :]
    pinky_mcp = landmarks[:, 17, :]
    features[:, 0] = (
        index_mcp[:, 0] * pinky_mcp[:, 1] - index_mcp[:, 1] * pinky_mcp[:, 0]
    )

    def _dist(a_index: int, b_index: int) -> np.ndarray:
        return np.linalg.norm(
            landmarks[:, a_index, :] - landmarks[:, b_index, :], axis=-1
        )

    features[:, 1] = _dist(8, 12)
    features[:, 2] = _dist(12, 16)
    features[:, 3] = _dist(16, 20)

    features[:, 4] = _dist(0, 8)
    features[:, 5] = _dist(0, 12)
    features[:, 6] = _dist(0, 16)
    features[:, 7] = _dist(0, 20)

    features[:, 8] = _dist(4, 0)
    features[:, 9] = _dist(4, 8)

    return features


def _interpolate(
    values: np.ndarray,
    source_time: np.ndarray,
    target_time: np.ndarray,
) -> np.ndarray:
    flattened = values.reshape(values.shape[0], -1)
    interpolated = np.empty((target_time.size, flattened.shape[1]), dtype=np.float64)
    for column in range(flattened.shape[1]):
        interpolated[:, column] = np.interp(
            target_time,
            source_time,
            flattened[:, column],
        )
    return interpolated.reshape((target_time.size, *values.shape[1:]))


def _orthonormalize(matrix: np.ndarray) -> np.ndarray:
    default_x = np.array([1.0, 0.0, 0.0], dtype=np.float64)
    x_axis = _unit_vector(matrix[0], default_x)

    y_candidate = matrix[1] - np.dot(matrix[1], x_axis) * x_axis
    if np.linalg.norm(y_candidate) < _MINIMUM_NORM:
        z_hint = matrix[2] - np.dot(matrix[2], x_axis) * x_axis
        if np.linalg.norm(z_hint) >= _MINIMUM_NORM:
            y_candidate = np.cross(z_hint, x_axis)
        else:
            y_candidate = _perpendicular_unit(x_axis)
    y_axis = _unit_vector(y_candidate, _perpendicular_unit(x_axis))
    z_axis = _unit_vector(np.cross(x_axis, y_axis), _perpendicular_unit(x_axis))

    if np.dot(z_axis, matrix[2]) < 0.0:
        y_axis = -y_axis
        z_axis = -z_axis
    return np.stack((x_axis, y_axis, z_axis))


def normalize(
    traj: Trajectory,
    *,
    resample_length: int = 45,
    hand_size: float | None = None,
) -> NormalizedTrajectory:
    if not traj.frames:
        raise ValueError("Cannot normalize an empty trajectory")
    if resample_length < 1:
        raise ValueError("resample_length must be at least 1")

    landmarks = np.asarray(
        [
            [[point.x, point.y, point.z] for point in frame.landmarks]
            for frame in traj.frames
        ],
        dtype=np.float64,
    )
    timestamps = np.asarray(
        [frame.timestamp for frame in traj.frames],
        dtype=np.float64,
    )
    frame_count = landmarks.shape[0]

    landmarks = landmarks - landmarks[:, 0:1, :]

    scale = hand_size
    if scale is None:
        scale = float(
            np.mean(np.linalg.norm(landmarks[:, 9, :] - landmarks[:, 0, :], axis=1))
        )
    landmarks = landmarks / max(float(scale), _MINIMUM_NORM)

    raw_features = _engineered_features(landmarks)

    orientation = np.empty((frame_count, 3, 3), dtype=np.float64)
    canonical = np.empty_like(landmarks)
    for index in range(frame_count):
        basis = _palm_basis(landmarks[index])
        orientation[index] = basis
        canonical[index] = landmarks[index] @ basis.T

    if frame_count > 1 and np.all(np.diff(timestamps) > 0.0):
        source_time = (timestamps - timestamps[0]) / (timestamps[-1] - timestamps[0])
    else:
        source_time = np.linspace(0.0, 1.0, frame_count, dtype=np.float64)
    target_time = np.linspace(0.0, 1.0, resample_length, dtype=np.float64)

    canonical = _interpolate(canonical, source_time, target_time)
    orientation = _interpolate(orientation, source_time, target_time)
    orientation = np.asarray(
        [_orthonormalize(matrix) for matrix in orientation],
        dtype=np.float64,
    )
    features = _interpolate(raw_features, source_time, target_time)

    velocity = np.zeros_like(canonical)
    velocity[1:] = canonical[1:] - canonical[:-1]

    return NormalizedTrajectory(
        canonical=canonical.astype(np.float32),
        orientation=orientation.astype(np.float32),
        velocity=velocity.astype(np.float32),
        features=features.astype(np.float32),
        frame_count=frame_count,
    )


def serialize(traj: Trajectory) -> bytes:
    values = np.empty((len(traj.frames), _COORDINATE_COUNT + 1), dtype=np.float32)
    for index, frame in enumerate(traj.frames):
        values[index, :_COORDINATE_COUNT] = np.asarray(
            [[point.x, point.y, point.z] for point in frame.landmarks],
            dtype=np.float32,
        ).reshape(_COORDINATE_COUNT)
        values[index, _COORDINATE_COUNT] = frame.timestamp
    return values.tobytes()


def deserialize(blob: bytes, handedness: str) -> Trajectory:
    values = np.frombuffer(blob, dtype=np.float32)
    row_width = _COORDINATE_COUNT + 1
    if values.size % row_width != 0:
        raise ValueError("Trajectory blob does not contain complete float32 frames")
    values = values.reshape((-1, row_width))

    frames = []
    for row in values:
        coordinates = row[:_COORDINATE_COUNT].reshape((_LANDMARK_COUNT, 3))
        landmarks = tuple(
            Point3D(x=float(point[0]), y=float(point[1]), z=float(point[2]))
            for point in coordinates
        )
        frames.append(
            LandmarkFrame(
                landmarks=landmarks,
                handedness=handedness,
                timestamp=float(row[_COORDINATE_COUNT]),
            )
        )
    return Trajectory(frames=tuple(frames), handedness=handedness)
