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
    assert np.allclose(a.features, b.features, atol=1e-5)

def test_scale_invariance():
    a = normalize(Trajectory((_straight_hand(scale=1.0, t=0.0), _straight_hand(scale=1.0, t=0.1)), "Right"))
    b = normalize(Trajectory((_straight_hand(scale=3.0, t=0.0), _straight_hand(scale=3.0, t=0.1)), "Right"))
    assert np.allclose(a.canonical, b.canonical, atol=1e-4)
    assert np.allclose(a.features, b.features, atol=1e-4)

def test_resample_to_fixed_length():
    frames = tuple(_straight_hand(t=i * 0.05) for i in range(10))
    out = normalize(Trajectory(frames, "Right"), resample_length=45)
    assert out.canonical.shape == (45, 21, 3)
    assert out.velocity.shape == (45, 21, 3)
    assert out.orientation.shape == (45, 3, 3)
    assert out.features.shape == (45, 10)
    assert out.frame_count == 10
    assert np.allclose(out.velocity[0], 0.0)

def test_palm_facing_feature_flips_sign_when_mirrored():
    """The engineered orientation feature (features[..., 0]) must capture
    palm-toward-vs-away, unlike ``canonical`` which is rotated into a
    palm-relative basis and is therefore orientation-invariant by design.

    Mirroring every landmark's x coordinate negates the signed cross product
    (5 - wrist) x (17 - wrist) -- the same 2D palm-facing signal
    StaticPoseRecognizer uses -- while leaving every pairwise distance (and
    so every other engineered feature) unchanged.
    """
    frames = tuple(_straight_hand(t=i * 0.05) for i in range(6))
    mirrored = tuple(
        LandmarkFrame(
            landmarks=tuple(
                Point3D(x=-point.x, y=point.y, z=point.z) for point in frame.landmarks
            ),
            handedness=frame.handedness,
            timestamp=frame.timestamp,
        )
        for frame in frames
    )

    original = normalize(Trajectory(frames, "Right"))
    flipped = normalize(Trajectory(mirrored, "Right"))

    assert np.allclose(original.features[:, 0], -flipped.features[:, 0], atol=1e-5)
    # Every distance-based feature (spread/fold/thumb) is unaffected.
    assert np.allclose(original.features[:, 1:], flipped.features[:, 1:], atol=1e-5)
    # But canonical (rotation-normalized) does not see the difference at all
    # -- this is exactly the gap the engineered features close.
    assert np.allclose(original.canonical, flipped.canonical, atol=1e-4)

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
