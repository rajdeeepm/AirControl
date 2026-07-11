from __future__ import annotations

import math
import time

import cv2
import numpy as np

from aircontrol.domain import EngineStatus, GestureSample, HandObservation, Pose


HAND_CONNECTIONS = (
    (0, 1), (1, 2), (2, 3), (3, 4),
    (0, 5), (5, 6), (6, 7), (7, 8),
    (5, 9), (9, 10), (10, 11), (11, 12),
    (9, 13), (13, 14), (14, 15), (15, 16),
    (13, 17), (0, 17), (17, 18), (18, 19), (19, 20),
)

POSE_HINTS = {
    Pose.NONE: "Place one hand inside the frame",
    Pose.UNKNOWN: "Relax or form a clear gesture",
    Pose.OPEN_PALM: "Open palm · hold to arm",
    Pose.FIST: "Fist · hold to pause",
    Pose.POINTER: "Index finger · move pointer",
    Pose.PINCH: "Pinch · click or drag",
    Pose.SCROLL: "Two fingers · move up/down",
    Pose.WINDOW_SWIPE: "Three fingers · swipe",
}


class GestureOverlay:
    def __init__(self, show_landmarks: bool = True):
        self.show_landmarks = show_landmarks
        self.last_action = ""
        self.last_action_at = 0.0

    def note_action(self, text: str) -> None:
        self.last_action = text
        self.last_action_at = time.monotonic()

    def draw(
        self,
        frame: np.ndarray,
        observation: HandObservation | None,
        sample: GestureSample | None,
        status: EngineStatus,
        fps: float,
        practice: bool,
    ) -> np.ndarray:
        canvas = frame.copy()
        height, width = canvas.shape[:2]

        cv2.rectangle(canvas, (22, 22), (width - 22, height - 22), (75, 95, 105), 1, cv2.LINE_AA)
        cv2.line(canvas, (40, 42), (90, 42), (255, 205, 65), 2, cv2.LINE_AA)

        if observation is not None and self.show_landmarks:
            self._draw_hand(canvas, observation, status.armed)

        panel_width = min(390, max(330, int(width * 0.39)))
        overlay = canvas.copy()
        cv2.rectangle(overlay, (36, 36), (36 + panel_width, height - 36), (12, 19, 24), -1)
        cv2.addWeighted(overlay, 0.82, canvas, 0.18, 0, canvas)

        self._text(canvas, "AIRCONTROL", (58, 72), 0.72, (242, 246, 247), 2)
        self._text(canvas, "LOCAL VISION", (58, 96), 0.40, (122, 148, 159), 1)
        state_color = (95, 235, 177) if status.armed else (105, 134, 147)
        state_label = "ARMED" if status.armed else "READY"
        if practice:
            state_label += " · PRACTICE"
            state_color = (80, 197, 255)
        self._pill(canvas, state_label, (58, 113), state_color)

        pose = sample.pose if sample is not None else Pose.NONE
        self._text(canvas, "DETECTED", (58, 171), 0.38, (107, 137, 149), 1)
        self._text(canvas, pose.value.upper(), (58, 202), 0.64, (255, 221, 110), 2)
        self._text(canvas, POSE_HINTS[pose], (58, 230), 0.44, (215, 225, 228), 1)

        self._text(canvas, status.status_text, (58, 273), 0.43, state_color, 1)
        if status.hold_progress > 0:
            self._progress(canvas, (58, 290), panel_width - 44, status.hold_progress, state_color)

        rows = (
            ("POINT", "Move cursor"),
            ("PINCH", "Click / hold to drag"),
            ("2 FINGERS", "Scroll"),
            ("3 FINGERS", "Apps / Task View / Desktop"),
        )
        row_y = 326
        for label, meaning in rows:
            self._text(canvas, label, (58, row_y), 0.36, (117, 150, 162), 1)
            self._text(canvas, meaning, (158, row_y), 0.40, (221, 229, 231), 1)
            row_y += 22

        mode = "SIMULATION ONLY" if practice else "WINDOWS CONTROL ENABLED"
        self._text(canvas, mode, (58, height - 70), 0.36, state_color, 1)
        self._text(canvas, "SPACE toggle  ·  Q quit  ·  video stays local", (58, height - 48), 0.36, (135, 157, 166), 1)

        self._text(canvas, f"{fps:0.0f} FPS", (width - 105, 46), 0.40, (150, 172, 180), 1)
        cv2.circle(canvas, (width - 128, 40), 4, (86, 225, 171), -1, cv2.LINE_AA)

        if status.hold_progress > 0 and observation is not None:
            wrist = observation.landmarks[0]
            center = (int(wrist.x * width), int(wrist.y * height))
            cv2.ellipse(
                canvas, center, (34, 34), -90, 0, 360 * status.hold_progress,
                state_color, 3, cv2.LINE_AA,
            )

        if self.last_action and time.monotonic() - self.last_action_at < 1.25:
            text_size = cv2.getTextSize(self.last_action, cv2.FONT_HERSHEY_SIMPLEX, 0.52, 1)[0]
            left = width - text_size[0] - 64
            top = height - 95
            cv2.rectangle(canvas, (left, top), (width - 34, height - 54), (20, 29, 34), -1)
            self._text(canvas, self.last_action, (left + 14, height - 68), 0.52, (245, 231, 170), 1)
        return canvas

    def _draw_hand(self, frame: np.ndarray, observation: HandObservation, armed: bool) -> None:
        height, width = frame.shape[:2]
        points = [(int(point.x * width), int(point.y * height)) for point in observation.landmarks]
        color = (255, 207, 72) if armed else (121, 151, 161)
        glow = frame.copy()
        for start, end in HAND_CONNECTIONS:
            cv2.line(glow, points[start], points[end], color, 5, cv2.LINE_AA)
        cv2.addWeighted(glow, 0.23, frame, 0.77, 0, frame)
        for start, end in HAND_CONNECTIONS:
            cv2.line(frame, points[start], points[end], color, 2, cv2.LINE_AA)
        for index, point in enumerate(points):
            radius = 5 if index in (4, 8, 12, 16, 20) else 3
            cv2.circle(frame, point, radius, (245, 246, 238), -1, cv2.LINE_AA)
            cv2.circle(frame, point, radius + 2, color, 1, cv2.LINE_AA)

    @staticmethod
    def _text(frame, text: str, origin, scale: float, color, thickness: int) -> None:
        cv2.putText(frame, text, origin, cv2.FONT_HERSHEY_SIMPLEX, scale, color, thickness, cv2.LINE_AA)

    @staticmethod
    def _pill(frame, text: str, origin, color) -> None:
        x, y = origin
        size = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.40, 1)[0]
        cv2.rectangle(frame, (x, y), (x + size[0] + 24, y + 26), (26, 37, 42), -1)
        cv2.rectangle(frame, (x, y), (x + size[0] + 24, y + 26), color, 1)
        cv2.circle(frame, (x + 11, y + 13), 3, color, -1, cv2.LINE_AA)
        cv2.putText(frame, text, (x + 20, y + 18), cv2.FONT_HERSHEY_SIMPLEX, 0.40, color, 1, cv2.LINE_AA)

    @staticmethod
    def _progress(frame, origin, width: int, progress: float, color) -> None:
        x, y = origin
        cv2.rectangle(frame, (x, y), (x + width, y + 5), (47, 61, 66), -1)
        cv2.rectangle(frame, (x, y), (x + math.floor(width * progress), y + 5), color, -1)
