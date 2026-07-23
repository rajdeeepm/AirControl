from __future__ import annotations

import math
from dataclasses import dataclass

from aircontrol.config import GestureConfig
from aircontrol.domain import GestureSample, HandObservation, Point2D, Point3D, Pose


FINGER_JOINTS = (
    (5, 6, 7, 8),     # index
    (9, 10, 11, 12),  # middle
    (13, 14, 15, 16), # ring
    (17, 18, 19, 20), # pinky
)


@dataclass(frozen=True, slots=True)
class _FingerMetrics:
    pip_angle: float
    dip_angle: float
    length_ratio: float
    reach_ratio: float


def _distance(a: Point3D, b: Point3D) -> float:
    return math.sqrt((a.x - b.x) ** 2 + (a.y - b.y) ** 2 + (a.z - b.z) ** 2)


def _distance_2d(a: Point3D, b: Point3D) -> float:
    return math.hypot(a.x - b.x, a.y - b.y)


def _angle(a: Point3D, vertex: Point3D, c: Point3D) -> float:
    first = (a.x - vertex.x, a.y - vertex.y, a.z - vertex.z)
    second = (c.x - vertex.x, c.y - vertex.y, c.z - vertex.z)
    first_len = math.sqrt(sum(value * value for value in first))
    second_len = math.sqrt(sum(value * value for value in second))
    if first_len == 0.0 or second_len == 0.0:
        return 0.0
    cosine = sum(a_value * b_value for a_value, b_value in zip(first, second))
    cosine /= first_len * second_len
    return math.degrees(math.acos(max(-1.0, min(1.0, cosine))))


def _average(points: tuple[Point3D, ...], indices: tuple[int, ...]) -> Point2D:
    return Point2D(
        x=sum(points[index].x for index in indices) / len(indices),
        y=sum(points[index].y for index in indices) / len(indices),
    )


def _finger_metrics(
    points: tuple[Point3D, ...],
    mcp_index: int,
    pip_index: int,
    dip_index: int,
    tip_index: int,
) -> _FingerMetrics:
    path_length = (
        _distance(points[mcp_index], points[pip_index])
        + _distance(points[pip_index], points[dip_index])
        + _distance(points[dip_index], points[tip_index])
    )
    direct_length = _distance(points[mcp_index], points[tip_index])
    pip_reach = _distance(points[pip_index], points[0])
    return _FingerMetrics(
        pip_angle=_angle(points[mcp_index], points[pip_index], points[dip_index]),
        dip_angle=_angle(points[pip_index], points[dip_index], points[tip_index]),
        length_ratio=direct_length / max(path_length, 1e-9),
        reach_ratio=_distance(points[tip_index], points[0]) / max(pip_reach, 1e-9),
    )


class StaticPoseRecognizer:
    """Turns 21 hand landmarks into a small, inspectable pose vocabulary."""

    def __init__(self, config: GestureConfig):
        self.config = config

    def recognize(self, observation: HandObservation | None) -> GestureSample | None:
        if observation is None:
            return None
        raw_points = observation.landmarks
        if len(raw_points) != 21:
            raise ValueError(f"Expected 21 hand landmarks, received {len(raw_points)}")

        width = max(1, observation.image_width)
        height = max(1, observation.image_height)
        y_scale = height / width
        points = tuple(Point3D(point.x, point.y * y_scale, point.z) for point in raw_points)
        world_points = observation.world_landmarks if len(observation.world_landmarks) == 21 else None

        wrist = points[0]
        palm_size = _distance_2d(wrist, points[9])
        palm_width = _distance_2d(points[5], points[17])
        center = _average(points, (0, 5, 9, 13, 17))
        pointer = Point2D(points[8].x, points[8].y)

        if palm_size < self.config.min_palm_size:
            return GestureSample(
                pose=Pose.UNKNOWN,
                pointer=pointer,
                center=center,
                palm_size=palm_size,
                extended_fingers=(False,) * 4,
                pinch_ratio=math.inf,
                finger_reaches=(0.0,) * 4,
            )

        extended: list[bool] = []
        curled: list[bool] = []
        reaches: list[float] = []
        for mcp_index, pip_index, dip_index, tip_index in FINGER_JOINTS:
            image_metrics = _finger_metrics(
                points, mcp_index, pip_index, dip_index, tip_index
            )
            world_metrics = (
                _finger_metrics(world_points, mcp_index, pip_index, dip_index, tip_index)
                if world_points is not None
                else None
            )

            def is_extended(metrics: _FingerMetrics) -> bool:
                return (
                    metrics.pip_angle >= self.config.extended_finger_angle
                    and metrics.dip_angle >= self.config.extended_dip_angle
                    and metrics.length_ratio >= self.config.extended_length_ratio
                    and metrics.reach_ratio >= self.config.finger_reach_ratio
                )

            finger_extended = is_extended(image_metrics) or (
                world_metrics is not None and is_extended(world_metrics)
            )
            curl_metrics = world_metrics or image_metrics
            reaches.append(curl_metrics.reach_ratio)
            finger_curled = (
                not finger_extended
                and curl_metrics.length_ratio <= self.config.curled_length_ratio
                and (
                    curl_metrics.pip_angle <= self.config.curled_finger_angle
                    or curl_metrics.dip_angle <= self.config.curled_dip_angle
                )
                and curl_metrics.reach_ratio <= self.config.curled_tip_reach_ratio
            )
            extended.append(finger_extended)
            curled.append(finger_curled)

        finger_state = tuple(extended)
        curled_state = tuple(curled)
        finger_reaches = (reaches[0], reaches[1], reaches[2], reaches[3])
        leading_reach = sum(finger_reaches[:2]) / 2
        swipe_reach = sum(finger_reaches[:3]) / 3
        has_leading_reach = leading_reach > 1e-9
        has_swipe_reach = swipe_reach > 1e-9
        ring_and_pinky_relatively_folded = (
            has_leading_reach
            and finger_reaches[2] <= self.config.fold_reach_ratio * leading_reach
            and finger_reaches[3] <= self.config.fold_reach_ratio * leading_reach
        )
        pinky_relatively_folded = (
            has_swipe_reach
            and finger_reaches[3] <= self.config.fold_reach_ratio * swipe_reach
        )
        leading_fingers_dominate = (
            has_leading_reach
            and min(finger_reaches[:2])
            >= self.config.scroll_separation_ratio * max(finger_reaches[2:])
        )
        pinch_ratio = _distance_2d(points[4], points[8]) / max(palm_size, 1e-9)
        open_palm_fingertip_gaps = (
            _distance_2d(points[8], points[12]),
            _distance_2d(points[12], points[16]),
            _distance_2d(points[16], points[20]),
        )
        open_palm_spread_ratio = max(open_palm_fingertip_gaps) / max(palm_size, 1e-9)
        fingers_together_for_open_palm = (
            open_palm_spread_ratio <= self.config.open_palm_max_finger_spread
        )
        if (
            pinch_ratio <= self.config.pinch_threshold_palms
            and finger_state[0]
            and not any(finger_state[1:])
        ):
            pose = Pose.PINCH
        elif (
            finger_state == (True, False, False, False)
            and sum(curled_state[1:]) >= 1
        ):
            pose = Pose.POINTER
        elif (
            not finger_state[2]
            and not finger_state[3]
            and ring_and_pinky_relatively_folded
            and ((finger_state[0] and finger_state[1]) or leading_fingers_dominate)
        ):
            pose = Pose.SCROLL
        elif all(finger_state[:3]) and pinky_relatively_folded:
            pose = Pose.WINDOW_SWIPE
        elif (
            finger_state == (True, True, True, True)
            and not pinky_relatively_folded
            and fingers_together_for_open_palm
        ):
            pose = Pose.OPEN_PALM
        elif curled_state == (True, True, True, True):
            pose = Pose.FIST
        elif (
            not any(finger_state)
            and all(reach > 1e-9 for reach in finger_reaches)
            and max(finger_reaches) <= self.config.fist_reach_max
        ):
            # Loose/mid-fold fist: fails the strict curl test but every tip
            # stays close to the wrist; a relaxed open hand sits well above
            # fist_reach_max and stays UNKNOWN.
            pose = Pose.FIST
        else:
            pose = Pose.UNKNOWN

        width_ratio = palm_width / palm_size
        orientation = (
            (points[5].x - wrist.x) * (points[17].y - wrist.y)
            - (points[5].y - wrist.y) * (points[17].x - wrist.x)
        ) / max(palm_size * palm_size, 1e-9)
        hand_sign = {"Left": 1.0, "Right": -1.0}.get(observation.handedness)
        mirror_sign = 1.0 if observation.input_is_mirrored else -1.0
        wrong_facing_side = (
            self.config.require_palm_facing
            and hand_sign is not None
            and orientation * hand_sign * mirror_sign < self.config.min_palm_orientation
        )
        if pose == Pose.OPEN_PALM and (
            width_ratio < self.config.min_palm_width_ratio
            or wrong_facing_side
            or not fingers_together_for_open_palm
        ):
            pose = Pose.UNKNOWN

        return GestureSample(
            pose=pose,
            pointer=pointer,
            center=center,
            palm_size=palm_size,
            extended_fingers=finger_state,
            pinch_ratio=pinch_ratio,
            finger_reaches=finger_reaches,
        )
