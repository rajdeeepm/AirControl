"""UI-agnostic gesture recognition, gating, and dispatch pipeline."""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Callable
from dataclasses import replace
from typing import Any

from aircontrol.arena import effective_t1
from aircontrol.buffer import RollingFrameBuffer
from aircontrol.clutch import ClutchStrategy, build_clutch
from aircontrol.config import AppConfig
from aircontrol.controller import ActionController
from aircontrol.density import IncidentalDensity
from aircontrol.domain import Action, ActionKind, GestureSample, HandObservation, Pose
from aircontrol.engine import GestureEngine
from aircontrol.gate import ConfidenceGate, GateDecision, heuristic_decision
from aircontrol.ipc import action_event, candidate_event, status_event
from aircontrol.matcher import DtwMatcher, TrajectoryMatcher
from aircontrol.metrics import Metrics
from aircontrol.profile import CalibrationProfile
from aircontrol.recognizer import StaticPoseRecognizer
from aircontrol.segmentation import SegmentationMachine
from aircontrol.settings import DEFAULTS, gate_t1, pointer_min_cutoff, pointer_pixels
from aircontrol.store import Store
from aircontrol.trajectory import frame_from_observation
from aircontrol.undo import UndoManager


PipelineEvent = dict[str, Any]
RELEASING_ACTIONS: frozenset[ActionKind] = frozenset({ActionKind.LEFT_UP})
_CLICK_RELEASE_DEBOUNCE_SECONDS = 0.04
_ALT_F4 = frozenset({0x12, 0x73})
_LWIN_L = frozenset({0x5B, 0x4C})
_CTRL_ALT_DELETE = frozenset({0x11, 0x12, 0x2E})
_VK_VOLUME_MUTE = 0xAD
_VK_VOLUME_DOWN = 0xAE
_VK_VOLUME_UP = 0xAF
_VK_MEDIA_NEXT_TRACK = 0xB0
_VK_MEDIA_PREVIOUS_TRACK = 0xB1
_VK_MEDIA_PLAY_PAUSE = 0xB3
_ACTION_CATEGORIES: dict[ActionKind, str] = {
    ActionKind.LEFT_DOWN: "click",
    ActionKind.LEFT_UP: "click",
    ActionKind.SCROLL: "scroll",
    ActionKind.SWITCH_NEXT: "window",
    ActionKind.SWITCH_PREVIOUS: "window",
    ActionKind.TASK_VIEW: "window",
    ActionKind.SHOW_DESKTOP: "window",
    ActionKind.MOVE_POINTER: "pointer",
    ActionKind.ESCAPE: "system",
}
_HOTKEY_CATEGORIES: dict[tuple[int, ...], str] = {
    (_VK_VOLUME_UP,): "volume",
    (_VK_VOLUME_DOWN,): "volume",
    (_VK_VOLUME_MUTE,): "mute",
    (_VK_MEDIA_PLAY_PAUSE,): "media",
    (_VK_MEDIA_NEXT_TRACK,): "media",
    (_VK_MEDIA_PREVIOUS_TRACK,): "media",
}
logger = logging.getLogger(__name__)


def action_category(
    kind: ActionKind,
    keys: tuple[int, ...] = (),
) -> str:
    """Return the honest semantic category derivable from an action."""
    if kind == ActionKind.HOTKEY:
        return _HOTKEY_CATEGORIES.get(keys, "hotkey")
    return _ACTION_CATEGORIES.get(kind, "hotkey")


class Pipeline:
    """Run one tracked-hand observation through the current heuristic stack."""

    def __init__(
        self,
        config: AppConfig,
        controller: ActionController,
        *,
        store: Store | None,
        metrics: Metrics,
        gate: ConfidenceGate,
        settings: dict[str, Any] | None = None,
        profile: CalibrationProfile | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.config = config
        self.controller = controller
        self.store = store
        self.metrics = metrics
        self.gate = gate
        self.settings: dict[str, Any] | None = None
        self._clutch: ClutchStrategy | None = None
        self._base_pointer_pixels_per_palm = config.input.pointer_pixels_per_palm
        self.matcher: TrajectoryMatcher | None = None
        if profile is not None and store is not None:
            matcher = DtwMatcher(store)
            matcher.refresh()
            self.matcher = matcher
        self.buffer = RollingFrameBuffer(config.pipeline.buffer_capacity)
        if profile is None:
            self.engine = GestureEngine(config.gestures)
            self._segmentation: SegmentationMachine | None = None
            self._density: IncidentalDensity | None = None
        else:
            self._clutch = build_clutch(config, profile)
            self.engine = GestureEngine(
                config.gestures,
                clutch=self._clutch,
            )
            self._segmentation = SegmentationMachine(profile.motion)
            self._density = self._restore_density(profile.incidental_features)
        self._click_hand_held = False
        self._click_hand_candidate_since: float | None = None
        self._click_hand_release_candidate_since: float | None = None
        self._click_hand_last_update_at: float | None = None
        self._click_hand_needs_release = False
        self._pointer_residual_x_pixels = 0.0
        self._pointer_residual_y_pixels = 0.0
        self._pointer_residual_pose = Pose.NONE
        if settings is not None:
            self.apply_settings(settings)
        self.recognizer = StaticPoseRecognizer(config.gestures)
        self.last_sample: GestureSample | None = None
        self._clock = clock
        self._undo = UndoManager(metrics, clock=clock)
        self._released = False

    def process(
        self,
        observation: HandObservation | None,
        now: float,
    ) -> list[PipelineEvent]:
        """Process one observation and return v1 events for its resulting state."""
        sample = self.recognizer.recognize(observation)
        self.last_sample = sample
        frame = (
            frame_from_observation(observation, now)
            if observation is not None
            else None
        )
        if frame is not None:
            self.buffer.append(frame)

        actions = self.engine.update(sample, now)
        self._sync_pointer_residual(sample)
        events = self._gated_action_events(actions, now)
        if self._segmentation is not None:
            segment = self._segmentation.update(frame, self.engine.armed, now)
            if segment is not None:
                self.metrics.note_candidate(armed=self.engine.armed)
                incidental_distance = (
                    self._density.distance(segment.trajectory)
                    if self._density is not None
                    else math.inf
                )
                result = (
                    self.matcher.match(segment.trajectory)
                    if self.matcher is not None
                    else None
                )
                has_match_library = (
                    result is not None
                    and bool(result.scores)
                )
                if result is not None and has_match_library:
                    top1 = result.top1
                    top2 = result.top2
                    self.metrics.begin_gesture()
                else:
                    top1 = 1.0
                    top2 = 0.0
                decision = self.gate.evaluate(
                    top1=top1,
                    top2=top2,
                    incidental_distance=incidental_distance,
                )
                if (
                    decision.fire
                    and result is not None
                    and has_match_library
                    and result.gesture_id is not None
                ):
                    offset = self._threshold_offset(result.gesture_id)
                    if offset > 0.0 and top1 < effective_t1(
                        self.gate.thresholds, offset
                    ):
                        decision = GateDecision(
                            fire=False,
                            confidence=decision.confidence,
                            reason="t1_offset",
                        )
                events.append(
                    candidate_event(
                        gate="fire" if decision.fire else "abstain",
                        reason=decision.reason,
                        confidence=decision.confidence,
                        ts=now,
                    )
                )
                if (
                    result is not None
                    and has_match_library
                    and decision.fire
                    and result.gesture_id is not None
                ):
                    action = self._mapped_action(result.gesture_id)
                    if action is not None:
                        events.append(
                            self._dispatch_matched(
                                action,
                                confidence=decision.confidence,
                                now=now,
                            )
                        )
        if self.config.metrics.cpu_sampling:
            self.metrics.sample_cpu()
        events.append(self.status())
        return events

    def process_hands(
        self,
        observations: tuple[HandObservation, ...],
        now: float,
    ) -> list[PipelineEvent]:
        """Process either the legacy best hand or coordinated pointer/click hands."""
        click_mode = (
            self.settings.get("click_mode", DEFAULTS["click_mode"])
            if self.settings is not None
            else DEFAULTS["click_mode"]
        )
        if click_mode != "two_hand" or len(observations) < 2:
            release_events = self._gated_action_events(
                self._release_click_hand(require_release=True),
                now,
            )
            observation = (
                max(observations, key=lambda item: item.confidence)
                if observations
                else None
            )
            release_events.extend(self.process(observation, now))
            return release_events

        pointer_index = self._pointer_hand_index(observations)
        pointer = observations[pointer_index]
        dominant = str(
            self.settings.get("dominant_hand", DEFAULTS["dominant_hand"])
            if self.settings is not None
            else DEFAULTS["dominant_hand"]
        ).strip().casefold()
        remaining_indices = [
            index for index in range(len(observations)) if index != pointer_index
        ]
        non_dominant_indices = [
            index
            for index in remaining_indices
            if observations[index].handedness.strip().casefold() != dominant
        ]
        click_index = max(
            non_dominant_indices or remaining_indices,
            key=lambda index: (observations[index].confidence, -index),
        )
        click = observations[click_index]

        # Dispatch pointer motion first so a same-frame click lands at the
        # cursor position established by the dominant hand.
        events = self.process(pointer, now)
        click_sample = self.recognizer.recognize(click)
        click_events = self._gated_action_events(
            self._click_hand_actions(click_sample, now),
            now,
        )
        insert_at = (
            len(events) - 1
            if events and events[-1].get("type") == "status"
            else len(events)
        )
        events[insert_at:insert_at] = click_events
        return events

    def _pointer_hand_index(
        self,
        observations: tuple[HandObservation, ...],
    ) -> int:
        dominant = str(
            self.settings.get("dominant_hand", DEFAULTS["dominant_hand"])
            if self.settings is not None
            else DEFAULTS["dominant_hand"]
        ).strip().casefold()
        matches = [
            index
            for index, observation in enumerate(observations)
            if observation.handedness.strip().casefold() == dominant
        ]
        if len(matches) == 1:
            return matches[0]

        def dominant_side_key(index: int) -> tuple[float, float, int]:
            observation = observations[index]
            anchors = tuple(
                observation.landmarks[anchor]
                for anchor in (0, 5, 9, 13, 17)
                if anchor < len(observation.landmarks)
            )
            center_x = (
                sum(point.x for point in anchors) / len(anchors)
                if anchors
                else 0.0
            )
            prefer_larger_x = (
                (dominant == "right") == observation.input_is_mirrored
            )
            side_score = center_x if prefer_larger_x else -center_x
            return side_score, observation.confidence, -index

        candidates = matches or list(range(len(observations)))
        return max(candidates, key=dominant_side_key)

    def _click_hand_actions(
        self,
        sample: GestureSample | None,
        now: float,
    ) -> list[Action]:
        last_update_at = self._click_hand_last_update_at
        self._click_hand_last_update_at = now
        if (
            last_update_at is not None
            and now - last_update_at > self.config.gestures.max_observation_gap_seconds
        ):
            release = self._release_click_hand(require_release=True)
            self._click_hand_last_update_at = now
            if release:
                return release

        if not self.engine.armed:
            return self._release_click_hand(require_release=True)

        pinch_ratio = sample.pinch_ratio if sample is not None else math.inf
        if not math.isfinite(pinch_ratio):
            pinch_ratio = math.inf
        if self._click_hand_needs_release:
            if pinch_ratio >= self.config.gestures.click_release_palms:
                self._click_hand_needs_release = False
            return []
        if self._click_hand_held:
            if pinch_ratio < self.config.gestures.click_release_palms:
                self._click_hand_release_candidate_since = None
                return []
            if self._click_hand_release_candidate_since is None:
                self._click_hand_release_candidate_since = now
                return []
            if (
                now - self._click_hand_release_candidate_since
                < _CLICK_RELEASE_DEBOUNCE_SECONDS
            ):
                return []
            return self._release_click_hand()

        if pinch_ratio > self.config.gestures.click_engage_palms:
            self._click_hand_candidate_since = None
            return []
        if self._click_hand_candidate_since is None:
            self._click_hand_candidate_since = now
            return []

        # Two consecutive engaging frames reject a one-frame landmark spike
        # without inheriting the slower general-pose stability window.
        self._click_hand_candidate_since = None
        self._click_hand_held = True
        return [Action(ActionKind.LEFT_DOWN)]

    def _release_click_hand(
        self,
        *,
        require_release: bool = False,
    ) -> list[Action]:
        had_unreleased_pinch = (
            self._click_hand_held or self._click_hand_candidate_since is not None
        )
        self._click_hand_candidate_since = None
        self._click_hand_release_candidate_since = None
        self._click_hand_last_update_at = None
        if require_release and had_unreleased_pinch:
            self._click_hand_needs_release = True
        elif not require_release:
            self._click_hand_needs_release = False
        if not self._click_hand_held:
            return []
        self._click_hand_held = False
        return [Action(ActionKind.LEFT_UP)]

    def status(self) -> PipelineEvent:
        status = self.engine.status()
        return status_event(
            armed=status.armed,
            raw_pose=status.raw_pose.value,
            active_pose=status.active_pose.value,
            hold_progress=status.hold_progress,
            hand_visible=status.hand_visible,
            status_text=status.status_text,
        )

    def toggle_arm(self, now: float) -> list[PipelineEvent]:
        if self._clutch is not None:
            self._clutch.set_armed(not self.engine.armed, now)
        events = self._forced_action_events(self.engine.manual_toggle(now), now)
        events.extend(
            self._gated_action_events(
                self._release_click_hand(require_release=True),
                now,
            )
        )
        events.append(self.status())
        return events

    def force_pause(self, reason: str) -> list[PipelineEvent]:
        now = self._clock()
        if self._clutch is not None:
            self._clutch.set_armed(False, now)
        events = self._forced_action_events(self.engine.force_pause(reason), now)
        events.extend(
            self._gated_action_events(
                self._release_click_hand(require_release=True),
                now,
            )
        )
        events.append(self.status())
        return events

    def undo(self) -> list[PipelineEvent]:
        action = self._undo.try_undo()
        if action is None:
            return []
        return self._forced_action_events([action], self._clock())

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        try:
            self.controller.release_all()
        finally:
            self.controller.close()

    def apply_settings(self, settings: dict[str, Any]) -> None:
        self.gate = ConfidenceGate(
            replace(
                self.gate.thresholds,
                t1_top1=gate_t1(settings["sensitivity"]),
            )
        )
        self.engine.config.pointer_min_cutoff = pointer_min_cutoff(
            settings["smoothing"]
        )
        self.controller.pointer_pixels_per_palm = pointer_pixels(
            self._base_pointer_pixels_per_palm,
            settings["cursor_speed"],
        )
        self.engine.suppress_pinch_click = settings["click_mode"] == "two_hand"
        self.settings = dict(settings)

    def _gated_action_events(
        self,
        actions: list[Action],
        now: float,
    ) -> list[PipelineEvent]:
        events: list[PipelineEvent] = []
        for action in actions:
            if action.kind in RELEASING_ACTIONS:
                description = self.controller.dispatch(action)
                self._undo.note_fire(action)
                self.metrics.note_action()
                events.append(
                    action_event(
                        kind=action.kind.value,
                        category=action_category(action.kind, action.keys),
                        confidence=1.0,
                        description=description or "",
                        ts=now,
                    )
                )
                continue

            self.metrics.note_candidate(armed=self.engine.armed)
            self.metrics.begin_gesture()
            decision = self._heuristic_gate_decision()
            events.append(
                candidate_event(
                    gate="fire" if decision.fire else "abstain",
                    reason=decision.reason,
                    confidence=decision.confidence,
                    ts=now,
                )
            )
            if not decision.fire:
                continue

            if action.kind == ActionKind.MOVE_POINTER:
                action = self._pointer_action_with_residual(action)
                if action is None:
                    continue

            events.append(
                self._dispatch_matched(
                    action,
                    confidence=decision.confidence,
                    now=now,
                )
            )
        return events

    def _sync_pointer_residual(self, sample: GestureSample | None) -> None:
        active_pose = self.engine.active_pose
        if active_pose != self._pointer_residual_pose:
            self._reset_pointer_residual()
            self._pointer_residual_pose = active_pose
        pinch_approach = (
            active_pose == Pose.POINTER
            and not self.engine.suppress_pinch_click
            and sample is not None
            and sample.pinch_ratio <= self.config.gestures.pinch_approach_palms
        )
        if (
            sample is None
            or active_pose not in {Pose.POINTER, Pose.PINCH}
            or pinch_approach
        ):
            self._reset_pointer_residual()

    def _pointer_action_with_residual(self, action: Action) -> Action | None:
        pixels_per_palm = self.controller.pointer_pixels_per_palm
        total_x = self._pointer_residual_x_pixels + action.dx * pixels_per_palm
        total_y = self._pointer_residual_y_pixels + action.dy * pixels_per_palm
        dx_pixels = round(total_x)
        dy_pixels = round(total_y)
        self._pointer_residual_x_pixels = total_x - dx_pixels
        self._pointer_residual_y_pixels = total_y - dy_pixels
        if dx_pixels == 0 and dy_pixels == 0:
            return None
        return replace(
            action,
            dx=dx_pixels / pixels_per_palm,
            dy=dy_pixels / pixels_per_palm,
        )

    def _reset_pointer_residual(self) -> None:
        self._pointer_residual_x_pixels = 0.0
        self._pointer_residual_y_pixels = 0.0

    def _mapped_action(self, gesture_id: int) -> Action | None:
        if self.store is None:
            return None
        try:
            mapping = self.store.mappings.for_gesture(gesture_id)
            if mapping is None or not mapping.enabled:
                return None
            kind = mapping.action.get("kind")
            if not isinstance(kind, str):
                raise ValueError("action kind must be a string")
            action_kind = ActionKind(kind)
            if action_kind == ActionKind.HOTKEY:
                keys = mapping.action.get("keys")
                if not isinstance(keys, list):
                    raise ValueError("hotkey keys must be a list")
                if not 1 <= len(keys) <= 4:
                    raise ValueError("hotkey keys must contain between 1 and 4 items")
                if any(type(key) is not int for key in keys):
                    raise ValueError("hotkey keys must be integers")
                if any(not 1 <= key <= 0xFE for key in keys):
                    raise ValueError("hotkey keys must be between 1 and 0xFE")
                action_keys = tuple(keys)
                key_set = frozenset(action_keys)
                risky = (
                    key_set == _ALT_F4
                    or key_set == _LWIN_L
                    or _CTRL_ALT_DELETE.issubset(key_set)
                )
                if risky and self.config.input.allow_risky_hotkeys is not True:
                    logger.warning(
                        "Ignoring risky hotkey mapping for gesture %s: %s",
                        gesture_id,
                        action_keys,
                    )
                    return None
                return Action(action_kind, keys=action_keys)

            amount = mapping.action.get("amount", 0)
            if type(amount) is not int:
                raise ValueError("action amount must be an integer")
            return Action(action_kind, amount=amount or 0)
        except (TypeError, ValueError) as exc:
            logger.warning(
                "Ignoring malformed gesture mapping for gesture %s: %s",
                gesture_id,
                exc,
            )
            return None

    def _dispatch_matched(
        self,
        action: Action,
        confidence: float,
        now: float,
    ) -> PipelineEvent:
        description = self.controller.dispatch(action)
        self._undo.note_fire(action)
        self.metrics.note_action()
        return action_event(
            kind=action.kind.value,
            category=action_category(action.kind, action.keys),
            confidence=confidence,
            description=description or "",
            ts=now,
        )

    def _forced_action_events(
        self,
        actions: list[Action],
        now: float,
    ) -> list[PipelineEvent]:
        """Dispatch safety/manual actions without allowing the gate to strand input."""
        events: list[PipelineEvent] = []
        confidence = heuristic_decision().confidence
        for action in actions:
            description = self.controller.dispatch(action)
            events.append(
                action_event(
                    kind=action.kind.value,
                    category=action_category(action.kind, action.keys),
                    confidence=confidence,
                    description=description or "",
                    ts=now,
                )
            )
        return events

    def _heuristic_gate_decision(self) -> GateDecision:
        heuristic = heuristic_decision()
        return self.gate.evaluate(
            top1=heuristic.confidence,
            top2=0.0,
            incidental_distance=math.inf,
        )

    def _threshold_offset(self, gesture_id: int) -> float:
        if self.store is None:
            return 0.0
        return self.store.gesture_stats.get(gesture_id).threshold_offset

    @staticmethod
    def _restore_density(
        rows: tuple[tuple[float, ...], ...],
    ) -> IncidentalDensity | None:
        if len(rows) < 2:
            return None
        try:
            return IncidentalDensity.from_rows(rows)
        except ValueError:
            return None


__all__ = ["Pipeline", "PipelineEvent", "action_category"]
