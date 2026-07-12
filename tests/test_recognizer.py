import pytest

from aircontrol.config import GestureConfig
from aircontrol.domain import HandObservation, Point3D, Pose
from aircontrol.engine import GestureEngine
from aircontrol.recognizer import StaticPoseRecognizer


FINGER_LAYOUT = {
    "index": (5, 6, 7, 8, 0.43, 0.62),
    "middle": (9, 10, 11, 12, 0.49, 0.60),
    "ring": (13, 14, 15, 16, 0.55, 0.62),
    "pinky": (17, 18, 19, 20, 0.61, 0.66),
}


def make_hand(
    extended=(),
    pinch=False,
    *,
    dx=0.0,
    dy=0.0,
    image_width=1,
    image_height=1,
    handedness="Left",
):
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
    if pinch:
        points[4] = Point3D(points[8].x + 0.01, points[8].y + 0.005)
    points = [Point3D(point.x + dx, point.y + dy, point.z) for point in points]
    return HandObservation(
        landmarks=tuple(points),
        handedness=handedness,
        confidence=0.99,
        image_width=image_width,
        image_height=image_height,
    )


def recognize(extended=(), pinch=False):
    return StaticPoseRecognizer(GestureConfig()).recognize(make_hand(extended, pinch)).pose


def make_naturally_bent_hand(extended):
    observation = make_hand(())
    points = list(observation.landmarks)
    for name, (mcp, pip, dip, tip, x, y) in FINGER_LAYOUT.items():
        if name in extended:
            points[mcp] = Point3D(x, y, 0.0)
            points[pip] = Point3D(x, y - 0.10, 0.0)
            points[dip] = Point3D(x + 0.04, y - 0.16, 0.0)
            points[tip] = Point3D(x + 0.08, y - 0.18, 0.0)
    landmarks = tuple(points)
    return HandObservation(
        landmarks=landmarks,
        handedness="Left",
        confidence=0.99,
        world_landmarks=landmarks,
        image_width=960,
        image_height=540,
        input_is_mirrored=True,
    )


def make_ambiguous_relaxed_hand():
    observation = make_hand(())
    points = list(observation.landmarks)
    for _name, (mcp, pip, dip, tip, x, y) in FINGER_LAYOUT.items():
        points[mcp] = Point3D(x, y)
        points[pip] = Point3D(x, y - 0.08)
        points[dip] = Point3D(x + 0.05, y - 0.13)
        points[tip] = Point3D(x + 0.02, y - 0.20)
    return HandObservation(tuple(points), "Left", 0.99)


def test_recognizes_core_pose_vocabulary():
    assert recognize(("index",)) == Pose.POINTER
    assert recognize(("index", "middle")) == Pose.SCROLL
    assert recognize(("index", "middle", "ring")) == Pose.WINDOW_SWIPE
    assert recognize(("index", "middle", "ring", "pinky")) == Pose.OPEN_PALM
    assert recognize(()) == Pose.FIST


def test_pinch_overrides_pointer_for_click_and_drag():
    assert recognize(("index",), pinch=True) == Pose.PINCH


def test_thumb_resting_on_a_closed_fist_does_not_click():
    assert recognize((), pinch=True) == Pose.FIST


def test_missing_hand_returns_none():
    assert StaticPoseRecognizer(GestureConfig()).recognize(None) is None


def test_motion_coordinates_are_aspect_corrected_to_equal_pixels():
    recognizer = StaticPoseRecognizer(GestureConfig())
    base = recognizer.recognize(
        make_hand(("index", "middle"), image_width=1600, image_height=900)
    )
    moved_x = recognizer.recognize(
        make_hand(
            ("index", "middle"), dx=90 / 1600, image_width=1600, image_height=900
        )
    )
    moved_y = recognizer.recognize(
        make_hand(
            ("index", "middle"), dy=90 / 900, image_width=1600, image_height=900
        )
    )

    assert moved_x.center.x - base.center.x == pytest.approx(
        moved_y.center.y - base.center.y
    )


def test_back_of_hand_geometry_cannot_arm():
    recognizer = StaticPoseRecognizer(GestureConfig(require_palm_facing=True))
    observation = make_hand(("index", "middle", "ring", "pinky"))
    mirrored_points = tuple(
        Point3D(1.0 - point.x, point.y, point.z) for point in observation.landmarks
    )
    back_of_right_hand = HandObservation(
        landmarks=mirrored_points,
        handedness="Left",
        confidence=0.99,
    )

    assert recognizer.recognize(back_of_right_hand).pose == Pose.UNKNOWN


def test_action_poses_are_not_gated_by_palm_side():
    recognizer = StaticPoseRecognizer(GestureConfig(require_palm_facing=True))
    observation = make_hand(("index",))
    mirrored_points = tuple(
        Point3D(1.0 - point.x, point.y, point.z) for point in observation.landmarks
    )

    result = recognizer.recognize(
        HandObservation(
            landmarks=mirrored_points,
            handedness="Left",
            confidence=0.99,
        )
    )

    assert result.pose == Pose.POINTER


def test_realistic_front_facing_open_palm_survives_noisy_world_landmarks():
    width, height = 1370, 1019
    screenshot_points = (
        (1138, 932), (1004, 900), (895, 817), (823, 729), (784, 638),
        (958, 615), (909, 490), (882, 417), (869, 350),
        (1041, 583), (1000, 438), (978, 345), (963, 269),
        (1125, 585), (1107, 439), (1097, 354), (1087, 269),
        (1207, 615), (1223, 518), (1231, 451), (1237, 383),
    )
    image_landmarks = tuple(
        Point3D(x / width, y / height, 0.0) for x, y in screenshot_points
    )
    noisy_world = tuple(Point3D(0.0, 0.0, 0.0) for _ in range(21))

    result = StaticPoseRecognizer(GestureConfig()).recognize(
        HandObservation(
            landmarks=image_landmarks,
            handedness="Left",
            confidence=0.99,
            world_landmarks=noisy_world,
            image_width=width,
            image_height=height,
        )
    )

    assert result.pose == Pose.OPEN_PALM
    assert result.extended_fingers == (True, True, True, True)


def test_foreshortened_index_uses_world_geometry_and_never_becomes_fist():
    full_pointer = make_hand(("index",))
    image_landmarks = list(full_pointer.landmarks)
    index_mcp = image_landmarks[5]
    image_landmarks[6] = Point3D(index_mcp.x, index_mcp.y - 0.025, 0.0)
    image_landmarks[7] = Point3D(index_mcp.x + 0.006, index_mcp.y - 0.04, 0.0)
    image_landmarks[8] = Point3D(index_mcp.x + 0.01, index_mcp.y - 0.05, 0.0)

    result = StaticPoseRecognizer(GestureConfig()).recognize(
        HandObservation(
            landmarks=tuple(image_landmarks),
            handedness="Right",
            confidence=0.99,
            world_landmarks=full_pointer.landmarks,
        )
    )

    assert result.pose == Pose.POINTER


def test_ambiguous_relaxed_hand_is_neutral_instead_of_false_fist():
    result = StaticPoseRecognizer(GestureConfig()).recognize(
        make_ambiguous_relaxed_hand()
    )

    assert result.pose == Pose.UNKNOWN


@pytest.mark.parametrize(
    ("fingers", "expected"),
    [
        (("index", "middle"), Pose.SCROLL),
        (("index", "middle", "ring"), Pose.WINDOW_SWIPE),
    ],
)
def test_multi_finger_poses_work_with_runtime_world_landmarks(fingers, expected):
    observation = make_hand(fingers)
    runtime_observation = HandObservation(
        landmarks=observation.landmarks,
        handedness=observation.handedness,
        confidence=observation.confidence,
        world_landmarks=observation.landmarks,
    )

    assert StaticPoseRecognizer(GestureConfig()).recognize(runtime_observation).pose == expected


@pytest.mark.parametrize(
    ("fingers", "expected"),
    [
        (("index",), Pose.POINTER),
        (("index", "middle"), Pose.SCROLL),
        (("index", "middle", "ring"), Pose.WINDOW_SWIPE),
        (("index", "middle", "ring", "pinky"), Pose.OPEN_PALM),
    ],
)
def test_naturally_bent_fingers_recognize_all_core_poses(fingers, expected):
    result = StaticPoseRecognizer(GestureConfig()).recognize(
        make_naturally_bent_hand(fingers)
    )

    assert result.pose == expected


def test_index_extended_toward_camera_uses_normalized_depth():
    observation = make_hand(())
    points = list(observation.landmarks)
    x, y = points[5].x, points[5].y
    points[5] = Point3D(x, y, 0.0)
    points[6] = Point3D(x + 0.002, y + 0.001, -0.10)
    points[7] = Point3D(x - 0.001, y - 0.001, -0.20)
    points[8] = Point3D(x + 0.001, y + 0.002, -0.30)

    result = StaticPoseRecognizer(GestureConfig()).recognize(
        HandObservation(tuple(points), "Left", 0.99)
    )

    assert result.pose == Pose.POINTER


def test_recognized_natural_open_palm_arms_the_engine():
    config = GestureConfig()
    recognizer = StaticPoseRecognizer(config)
    engine = GestureEngine(config)
    sample = recognizer.recognize(
        make_naturally_bent_hand(("index", "middle", "ring", "pinky"))
    )

    now = 0.0
    while now <= config.arm_hold_seconds + 0.1:
        engine.update(sample, now)
        now += 0.05

    assert sample.pose == Pose.OPEN_PALM
    assert engine.armed


def test_sustained_ambiguous_hand_cannot_pause_an_armed_engine():
    config = GestureConfig()
    recognizer = StaticPoseRecognizer(config)
    engine = GestureEngine(config)
    engine.manual_toggle(now=0.0)
    sample = recognizer.recognize(make_ambiguous_relaxed_hand())

    now = 0.05
    while now <= config.pause_hold_seconds + 0.2:
        engine.update(sample, now)
        now += 0.05

    assert sample.pose == Pose.UNKNOWN
    assert engine.armed


def make_reach_regression_hand(finger_shapes, *, handedness="Left"):
    observation = make_hand(())
    points = list(observation.landmarks)
    for name, (mcp, pip, dip, tip, x, y) in FINGER_LAYOUT.items():
        points[mcp] = Point3D(x, y)
        shape = finger_shapes[name]
        if shape == "straight":
            points[pip] = Point3D(x, y - 0.13)
            points[dip] = Point3D(x, y - 0.24)
            points[tip] = Point3D(x, y - 0.34)
        elif shape == "half":
            points[pip] = Point3D(x, y - 0.11)
            points[dip] = Point3D(x + 0.02, y - 0.17, z=0.03)
            points[tip] = Point3D(x + 0.045, y - 0.19, z=0.06)
        elif shape == "foreshort":
            points[pip] = Point3D(x, y - 0.09, z=-0.05)
            points[dip] = Point3D(x + 0.005, y - 0.13, z=-0.11)
            points[tip] = Point3D(x + 0.01, y - 0.15, z=-0.17)
        elif shape == "curl":
            points[pip] = Point3D(x, y - 0.07)
            points[dip] = Point3D(x + 0.035, y - 0.01)
            points[tip] = Point3D(x + 0.018, y + 0.045)
        else:
            raise ValueError(f"Unknown finger shape: {shape}")
    return HandObservation(
        landmarks=tuple(points),
        handedness=handedness,
        confidence=0.99,
        image_width=960,
        image_height=540,
        input_is_mirrored=True,
    )


def test_half_folded_pinky_with_three_straight_fingers_is_window_swipe():
    observation = make_reach_regression_hand(
        {
            "index": "straight",
            "middle": "straight",
            "ring": "straight",
            "pinky": "half",
        }
    )

    result = StaticPoseRecognizer(GestureConfig()).recognize(observation)

    assert result.pose == Pose.WINDOW_SWIPE


def test_relatively_folded_pinky_never_becomes_open_palm_when_binary_extended():
    observation = make_reach_regression_hand(
        {
            "index": "straight",
            "middle": "straight",
            "ring": "straight",
            "pinky": "half",
        }
    )
    recognizer = StaticPoseRecognizer(GestureConfig(extended_finger_angle=130.0))

    result = recognizer.recognize(observation)

    assert result.extended_fingers == (True, True, True, True)
    assert result.pose != Pose.OPEN_PALM


def test_half_folded_ring_and_pinky_with_straight_leading_fingers_is_scroll():
    observation = make_reach_regression_hand(
        {
            "index": "straight",
            "middle": "straight",
            "ring": "half",
            "pinky": "half",
        }
    )

    result = StaticPoseRecognizer(GestureConfig()).recognize(observation)

    assert result.pose == Pose.SCROLL


def test_half_raised_leading_fingers_with_curled_ring_and_pinky_is_scroll():
    observation = make_reach_regression_hand(
        {
            "index": "half",
            "middle": "half",
            "ring": "curl",
            "pinky": "curl",
        }
    )

    result = StaticPoseRecognizer(GestureConfig()).recognize(observation)

    assert result.pose == Pose.SCROLL


def test_foreshortened_leading_fingers_with_curled_ring_and_pinky_is_scroll():
    observation = make_reach_regression_hand(
        {
            "index": "foreshort",
            "middle": "foreshort",
            "ring": "curl",
            "pinky": "curl",
        }
    )

    result = StaticPoseRecognizer(GestureConfig()).recognize(observation)

    assert result.pose == Pose.SCROLL


def test_reach_regression_all_curled_remains_fist():
    observation = make_reach_regression_hand(
        {
            "index": "curl",
            "middle": "curl",
            "ring": "curl",
            "pinky": "curl",
        }
    )

    result = StaticPoseRecognizer(GestureConfig()).recognize(observation)

    assert result.pose == Pose.FIST


def test_reach_regression_all_straight_remains_open_palm():
    observation = make_reach_regression_hand(
        {
            "index": "straight",
            "middle": "straight",
            "ring": "straight",
            "pinky": "straight",
        }
    )

    result = StaticPoseRecognizer(GestureConfig()).recognize(observation)

    assert result.pose == Pose.OPEN_PALM


def test_reach_based_action_poses_are_identical_for_left_and_right_hands():
    cases = (
        (
            {
                "index": "straight",
                "middle": "straight",
                "ring": "straight",
                "pinky": "half",
            },
            Pose.WINDOW_SWIPE,
        ),
        (
            {
                "index": "straight",
                "middle": "straight",
                "ring": "half",
                "pinky": "half",
            },
            Pose.SCROLL,
        ),
        (
            {
                "index": "half",
                "middle": "half",
                "ring": "curl",
                "pinky": "curl",
            },
            Pose.SCROLL,
        ),
        (
            {
                "index": "foreshort",
                "middle": "foreshort",
                "ring": "curl",
                "pinky": "curl",
            },
            Pose.SCROLL,
        ),
    )
    recognizer = StaticPoseRecognizer(GestureConfig())

    for finger_shapes, expected in cases:
        poses = {
            recognizer.recognize(
                make_reach_regression_hand(finger_shapes, handedness=handedness)
            ).pose
            for handedness in ("Left", "Right")
        }
        assert poses == {expected}


def test_loosely_folded_fist_still_pauses():
    """A realistic mid-fold fist (reaches ~0.8-1.1) must classify as FIST.

    The strict curl test alone misses it, which read as Neutral live and made
    the fist pause unreachable. The relaxed ambiguous hand (reaches ~1.4)
    must stay UNKNOWN — see test_relaxed_hand_is_not_a_pose.
    """
    observation = make_hand(())
    points = list(observation.landmarks)
    for name, (mcp, pip, dip, tip, x, y) in FINGER_LAYOUT.items():
        points[mcp] = Point3D(x, y, 0.0)
        points[pip] = Point3D(x, y - 0.10, 0.0)
        points[dip] = Point3D(x + 0.03, y - 0.09, -0.02)
        points[tip] = Point3D(x + 0.05, y - 0.055, -0.03)
    folded = HandObservation(
        landmarks=tuple(points),
        handedness="Left",
        confidence=0.99,
        image_width=960,
        image_height=540,
        input_is_mirrored=True,
    )
    result = StaticPoseRecognizer(GestureConfig()).recognize(folded)
    assert result.pose == Pose.FIST
