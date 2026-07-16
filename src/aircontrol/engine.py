from __future__ import annotations

import math
from typing import TYPE_CHECKING

from aircontrol.config import GestureConfig
from aircontrol.domain import Action, ActionKind, EngineStatus, GestureSample, Point2D, Pose
from aircontrol.onefilter import OneEuroFilter

if TYPE_CHECKING:
    from aircontrol.clutch import ClutchStrategy


ACTIVE_POSES = frozenset({Pose.POINTER, Pose.PINCH, Pose.SCROLL, Pose.WINDOW_SWIPE})


class GestureEngine:
    """Temporal state machine that turns stable poses and motion into safe actions."""

    def __init__(
        self,
        config: GestureConfig,
        *,
        clutch: "ClutchStrategy | None" = None,
        suppress_pinch_click: bool = False,
    ):
        self.config = config
        self._clutch = clutch
        self.suppress_pinch_click = suppress_pinch_click
        self._clutch_progress = 0.0
        self.armed = False
        self.raw_pose = Pose.NONE
        self.active_pose = Pose.NONE
        self._candidate_pose = Pose.NONE
        self._candidate_since: float | None = None
        self._hold_pose = Pose.NONE
        self._hold_since: float | None = None
        self._hold_anchor: Point2D | None = None
        self._hold_palm_size = 1.0
        self._hold_progress = 0.0
        self._last_seen_at: float | None = None
        self._last_update_at: float | None = None
        self._tracking_interrupted = False
        self._pointer_filter: OneEuroFilter | None = None
        self._smoothed_pointer: Point2D | None = None
        self._last_center: Point2D | None = None
        self._anchor: Point2D | None = None
        self._anchor_palm_size = 1.0
        self._active_since: float | None = None
        self._scroll_accumulator = 0.0
        self._discrete_fired = False
        self._left_held = False
        self._pinch_drag_anchor: Point2D | None = None
        self._pinch_drag_palm_size = 1.0
        self._status_text = "Show an open palm to start"

    def update(self, sample: GestureSample | None, now: float) -> list[Action]:
        actions: list[Action] = []
        self.raw_pose = Pose.NONE if sample is None else sample.pose

        if self._clutch is not None:
            state = self._clutch.update(sample, now)
            self._clutch_progress = state.progress
            if state.armed != self.armed:
                actions.extend(self._set_armed(state.armed, state.status_text))
            else:
                self._status_text = state.status_text

        if (
            self._last_update_at is not None
            and now - self._last_update_at > self.config.max_observation_gap_seconds
        ):
            actions.extend(self._exit_active())
            self._reset_candidate()
            self._reset_hold()
        self._last_update_at = now

        if sample is None:
            self._reset_hold()
            self._reset_candidate()
            self._tracking_interrupted = True
            if self._last_seen_at is not None:
                absent_for = now - self._last_seen_at
                if absent_for >= self.config.lost_hand_grace_seconds:
                    actions.extend(self._exit_active())
                if (
                    self._clutch is None
                    and self.armed
                    and absent_for >= self.config.auto_pause_seconds
                ):
                    actions.extend(self._set_armed(False, "Paused — hand left the camera"))
            return self._finish_update(actions, now)

        was_interrupted = self._tracking_interrupted
        self._tracking_interrupted = False
        self._last_seen_at = now
        if self._clutch is None:
            actions.extend(self._handle_safety_hold(sample, now))
        if not self.armed:
            self._reset_candidate()
            return self._finish_update(actions, now)

        requested_pose = sample.pose if sample.pose in ACTIVE_POSES else Pose.NONE
        if self.suppress_pinch_click and requested_pose == Pose.PINCH:
            requested_pose = Pose.POINTER

        if was_interrupted and self.active_pose == requested_pose and requested_pose != Pose.NONE:
            self._reanchor_active(sample, now)
            return self._finish_update(actions, now)

        if self.active_pose == Pose.PINCH and requested_pose != Pose.PINCH:
            actions.extend(self._exit_active())

        if requested_pose != self._candidate_pose:
            if self.active_pose != Pose.NONE and requested_pose != self.active_pose:
                actions.extend(self._exit_active())
            self._candidate_pose = requested_pose
            self._candidate_since = now
            return self._finish_update(actions, now)

        if requested_pose == Pose.NONE:
            return self._finish_update(actions, now)

        if self.active_pose != requested_pose:
            if self._candidate_since is None or now - self._candidate_since < self.config.stability_seconds:
                return self._finish_update(actions, now)
            actions.extend(self._exit_active())
            self._enter_active(requested_pose, sample, now)
            if requested_pose == Pose.PINCH:
                actions.append(Action(ActionKind.LEFT_DOWN))
                self._left_held = True
            return self._finish_update(actions, now)

        actions.extend(self._update_active(sample, now))
        return self._finish_update(actions, now)

    def manual_toggle(self, now: float | None = None) -> list[Action]:
        if self.armed:
            return self._set_armed(False, "Paused manually")
        if now is not None:
            self._last_seen_at = now
        return self._set_armed(True, "Armed — fist pauses control")

    def force_pause(self, reason: str = "Paused for safety") -> list[Action]:
        return self._set_armed(False, reason)

    def status(self) -> EngineStatus:
        return EngineStatus(
            armed=self.armed,
            raw_pose=self.raw_pose,
            active_pose=self.active_pose,
            hold_progress=(
                self._clutch_progress if self._clutch is not None else self._hold_progress
            ),
            hand_visible=self.raw_pose != Pose.NONE,
            status_text=self._status_text,
        )

    def _finish_update(self, actions: list[Action], now: float) -> list[Action]:
        if self._clutch is not None and self.armed and actions:
            self._clutch.notify_gesture(now)
        return actions

    def _handle_safety_hold(self, sample: GestureSample, now: float) -> list[Action]:
        target = Pose.FIST if self.armed else Pose.OPEN_PALM
        duration = self.config.pause_hold_seconds if self.armed else self.config.arm_hold_seconds
        if sample.pose != target:
            self._reset_hold()
            return []
        if self._hold_pose != target or self._hold_since is None:
            self._hold_pose = target
            self._hold_since = now
            self._hold_anchor = sample.center
            self._hold_palm_size = max(sample.palm_size, self.config.min_palm_size)
            self._hold_progress = 0.0
            return []

        if self._hold_anchor is not None:
            motion = math.hypot(
                sample.center.x - self._hold_anchor.x,
                sample.center.y - self._hold_anchor.y,
            ) / self._hold_palm_size
            if motion > self.config.hold_motion_limit_palms:
                self._hold_since = now
                self._hold_anchor = sample.center
                self._hold_palm_size = max(sample.palm_size, self.config.min_palm_size)
                self._hold_progress = 0.0
                return []

        self._hold_progress = min(1.0, (now - self._hold_since) / duration)
        if self._hold_progress < 1.0:
            self._status_text = "Hold steady to pause" if self.armed else "Hold steady to arm"
            return []

        self._reset_hold()
        if self.armed:
            return self._set_armed(False, "Paused — open palm arms again")
        return self._set_armed(True, "Armed — fist pauses control")

    def _set_armed(self, armed: bool, text: str) -> list[Action]:
        actions = self._exit_active()
        self.armed = armed
        self._status_text = text
        self._reset_candidate()
        self._reset_hold()
        return actions

    def _enter_active(self, pose: Pose, sample: GestureSample, now: float) -> None:
        self.active_pose = pose
        self._prime_pointer_filter(sample.pointer, now)
        self._last_center = sample.center
        self._anchor = sample.center
        self._anchor_palm_size = max(sample.palm_size, self.config.min_palm_size)
        self._active_since = now
        self._scroll_accumulator = 0.0
        self._discrete_fired = False
        if pose == Pose.PINCH:
            self._pinch_drag_anchor = sample.pointer
            self._pinch_drag_palm_size = max(
                sample.palm_size, self.config.min_palm_size
            )

    def _exit_active(self) -> list[Action]:
        actions: list[Action] = []
        if self._left_held:
            actions.append(Action(ActionKind.LEFT_UP))
            self._left_held = False
        self.active_pose = Pose.NONE
        self._pointer_filter = None
        self._smoothed_pointer = None
        self._last_center = None
        self._anchor = None
        self._active_since = None
        self._scroll_accumulator = 0.0
        self._discrete_fired = False
        self._pinch_drag_anchor = None
        self._pinch_drag_palm_size = 1.0
        return actions

    def _reanchor_active(self, sample: GestureSample, now: float) -> None:
        """Resume after a brief tracking gap without replaying unseen motion."""
        self._prime_pointer_filter(sample.pointer, now)
        self._last_center = sample.center
        self._anchor = sample.center
        self._anchor_palm_size = max(sample.palm_size, self.config.min_palm_size)
        self._active_since = now
        self._scroll_accumulator = 0.0
        self._candidate_pose = self.active_pose
        self._candidate_since = now
        if self.active_pose == Pose.PINCH and self._pinch_drag_anchor is not None:
            self._pinch_drag_anchor = sample.pointer
            self._pinch_drag_palm_size = max(
                sample.palm_size, self.config.min_palm_size
            )

    def _update_active(self, sample: GestureSample, now: float) -> list[Action]:
        if self.active_pose == Pose.POINTER:
            if (
                not self.suppress_pinch_click
                and sample.pinch_ratio <= self.config.pinch_approach_palms
            ):
                # Freeze the visible cursor while consuming the changing
                # fingertip coordinate so an abandoned approach cannot replay
                # the suppressed motion later.
                self._prime_pointer_filter(sample.pointer, now)
                return []
            action = self._pointer_action(sample, now)
            return [] if action is None else [action]
        if self.active_pose == Pose.PINCH:
            if self._pinch_drag_anchor is not None:
                motion = math.hypot(
                    sample.pointer.x - self._pinch_drag_anchor.x,
                    sample.pointer.y - self._pinch_drag_anchor.y,
                ) / self._pinch_drag_palm_size
                if motion <= self.config.pinch_drag_release_palms:
                    return []
                self._pinch_drag_anchor = None
            action = self._pointer_action(sample, now)
            return [] if action is None else [action]
        if self.active_pose == Pose.SCROLL:
            action = self._scroll_action(sample)
            return [] if action is None else [action]
        if self.active_pose == Pose.WINDOW_SWIPE:
            action = self._window_action(sample, now)
            return [] if action is None else [action]
        return []

    def _pointer_action(self, sample: GestureSample, now: float) -> Action | None:
        if self._pointer_filter is None or self._smoothed_pointer is None:
            self._prime_pointer_filter(sample.pointer, now)
            return None
        parameters = self._pointer_filter_parameters()
        if self._pointer_filter.parameters != parameters:
            self._pointer_filter.configure(
                min_cutoff=parameters[0],
                beta=parameters[1],
                d_cutoff=parameters[2],
            )
        previous = self._smoothed_pointer
        filtered_x, filtered_y = self._pointer_filter.update(
            sample.pointer.x,
            sample.pointer.y,
            now,
        )
        current = Point2D(filtered_x, filtered_y)
        self._smoothed_pointer = current
        palm = max(sample.palm_size, self.config.min_palm_size)
        dx = (current.x - previous.x) / palm
        dy = (current.y - previous.y) / palm
        magnitude = math.hypot(dx, dy)
        if magnitude == 0.0:
            return None
        if magnitude > self.config.pointer_max_step_palms:
            scale = self.config.pointer_max_step_palms / magnitude
            dx *= scale
            dy *= scale
            limit = self.config.pointer_max_step_palms
            dx = max(-limit, min(limit, dx))
            dy = max(-limit, min(limit, dy))
        return Action(ActionKind.MOVE_POINTER, dx=dx, dy=dy)

    def _pointer_filter_parameters(self) -> tuple[float, float, float]:
        return (
            self.config.pointer_min_cutoff,
            self.config.pointer_beta,
            self.config.pointer_dcutoff,
        )

    def _prime_pointer_filter(self, pointer: Point2D, now: float) -> None:
        self._pointer_filter = OneEuroFilter(*self._pointer_filter_parameters())
        filtered_x, filtered_y = self._pointer_filter.update(pointer.x, pointer.y, now)
        self._smoothed_pointer = Point2D(filtered_x, filtered_y)

    def _scroll_action(self, sample: GestureSample) -> Action | None:
        if self._last_center is None:
            self._last_center = sample.center
            return None
        dy = sample.center.y - self._last_center.y
        self._last_center = sample.center
        palm = max(sample.palm_size, self.config.min_palm_size)
        direction = 1.0 if self.config.natural_scroll else -1.0
        delta_notches = direction * (dy / palm) * self.config.scroll_notches_per_palm
        max_delta = self.config.max_scroll_notches_per_frame
        delta_notches = max(-max_delta, min(max_delta, delta_notches))
        self._scroll_accumulator += delta_notches
        if -1.0 < self._scroll_accumulator < 1.0:
            return None
        amount = math.floor(self._scroll_accumulator) if self._scroll_accumulator > 0 else math.ceil(self._scroll_accumulator)
        self._scroll_accumulator -= amount
        return Action(ActionKind.SCROLL, amount=int(amount))

    def _window_action(self, sample: GestureSample, now: float) -> Action | None:
        if self._discrete_fired or self._anchor is None:
            return None
        dx = (sample.center.x - self._anchor.x) / self._anchor_palm_size
        dy = (sample.center.y - self._anchor.y) / self._anchor_palm_size
        if math.hypot(dx, dy) < self.config.swipe_rest_epsilon_palms:
            # The hand is at rest: slide the motion window forward so the
            # swipe budget starts at motion onset, not at pose entry.
            self._anchor = sample.center
            self._anchor_palm_size = max(sample.palm_size, self.config.min_palm_size)
            self._active_since = now
            return None
        if self._active_since is not None and now - self._active_since > self.config.swipe_max_seconds:
            self._discrete_fired = True
            return None
        threshold = self.config.swipe_threshold_palms
        ratio = self.config.swipe_axis_ratio
        action: Action | None = None
        if abs(dx) >= threshold and abs(dx) >= abs(dy) * ratio:
            action = Action(ActionKind.SWITCH_NEXT if dx < 0 else ActionKind.SWITCH_PREVIOUS)
        elif abs(dy) >= threshold and abs(dy) >= abs(dx) * ratio:
            action = Action(ActionKind.TASK_VIEW if dy < 0 else ActionKind.SHOW_DESKTOP)
        if action is not None:
            self._discrete_fired = True
        return action

    def _reset_candidate(self) -> None:
        self._candidate_pose = Pose.NONE
        self._candidate_since = None

    def _reset_hold(self) -> None:
        self._hold_pose = Pose.NONE
        self._hold_since = None
        self._hold_anchor = None
        self._hold_palm_size = 1.0
        self._hold_progress = 0.0
