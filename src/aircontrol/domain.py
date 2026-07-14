from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


@dataclass(frozen=True, slots=True)
class Point2D:
    x: float
    y: float


@dataclass(frozen=True, slots=True)
class Point3D:
    x: float
    y: float
    z: float = 0.0


@dataclass(frozen=True, slots=True)
class HandObservation:
    landmarks: tuple[Point3D, ...]
    handedness: str = "Unknown"
    confidence: float = 0.0
    world_landmarks: tuple[Point3D, ...] = ()
    image_width: int = 1
    image_height: int = 1
    input_is_mirrored: bool = True


class Pose(str, Enum):
    NONE = "No hand"
    UNKNOWN = "Neutral"
    OPEN_PALM = "Open palm"
    FIST = "Fist"
    POINTER = "Pointer"
    PINCH = "Pinch"
    SCROLL = "Two-finger scroll"
    WINDOW_SWIPE = "Three-finger swipe"


@dataclass(frozen=True, slots=True)
class GestureSample:
    pose: Pose
    pointer: Point2D
    center: Point2D
    palm_size: float
    extended_fingers: tuple[bool, bool, bool, bool]
    pinch_ratio: float
    finger_reaches: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)


class ActionKind(str, Enum):
    MOVE_POINTER = "move_pointer"
    LEFT_DOWN = "left_down"
    LEFT_UP = "left_up"
    SCROLL = "scroll"
    SWITCH_NEXT = "switch_next"
    SWITCH_PREVIOUS = "switch_previous"
    TASK_VIEW = "task_view"
    SHOW_DESKTOP = "show_desktop"
    ESCAPE = "escape"
    HOTKEY = "hotkey"


@dataclass(frozen=True, slots=True)
class Action:
    kind: ActionKind
    dx: float = 0.0
    dy: float = 0.0
    amount: int = 0
    keys: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class EngineStatus:
    armed: bool
    raw_pose: Pose
    active_pose: Pose
    hold_progress: float
    hand_visible: bool
    status_text: str
