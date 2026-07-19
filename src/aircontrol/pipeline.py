"""UI-agnostic gesture recognition, gating, and dispatch pipeline."""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Callable
from dataclasses import replace
from enum import Enum
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
from aircontrol.settings import (
    DEFAULTS,
    gate_t1,
    gesture_config_updates,
    pointer_pixels,
)
from aircontrol.store import Store
from aircontrol.trajectory import frame_from_observation
from aircontrol.undo import UndoManager


PipelineEvent = dict[str, Any]
RELEASING_ACTIONS: frozenset[ActionKind] = frozenset({ActionKind.LEFT_UP})
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


def _observation_center_x(observation: HandObservation) -> float:
    """Return the observation's palm center in input-frame coordinates."""
    anchors = tuple(
        observation.landmarks[index]
        for index in (0, 5, 9, 13, 17)
        if index < len(observation.landmarks)
    )
    return (
        sum(point.x for point in anchors) / len(anchors)
        if anchors
        else 0.0
    )


class _ModifierMode(str, Enum):
    """Mode selected by the non-dominant (modifier) hand in two-hand mode.

    The dominant hand does all pinching. The modifier hand only selects how
    that pinch behaves:
    - NEUTRAL: pointer moves; the dominant pinch does nothing (no click
      without an explicit modifier, so clicks are always deliberate).
    - LOCK (open palm): the cursor freezes where the dominant hand points;
      the dominant pinch then left-clicks at that exact position.
    - DRAG (fist): the dominant pinch holds the button and movement drags.
    Only the DOMINANT hand's fist disarms (via the clutch); the modifier
    hand never reaches the clutch.
    """

    NEUTRAL = "neutral"
    LOCK = "lock"
    DRAG = "drag"


_MODIFIER_POSES: dict[Pose, _ModifierMode] = {
    Pose.OPEN_PALM: _ModifierMode.LOCK,
    Pose.FIST: _ModifierMode.DRAG,
}
# Consecutive frames the modifier pose must persist before the mode switches;
# rejects one-frame landmark flickers without adding perceptible latency.
_MODIFIER_DEBOUNCE_FRAMES = 2


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
        self._clock = clock
        self._modifier_mode = _ModifierMode.NEUTRAL
        self._modifier_candidate: _ModifierMode | None = None
        self._modifier_candidate_frames = 0
        self._pointer_button_down = False
        self._freeze_pointer_motion = False
        self._pointer_residual_x_pixels = 0.0
        self._pointer_residual_y_pixels = 0.0
        self._pointer_residual_pose = Pose.NONE
        if settings is not None:
            self.apply_settings(settings)
        self.recognizer = StaticPoseRecognizer(config.gestures)
        self.last_sample: GestureSample | None = None
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
        if click_mode != "two_hand":
            # Single-hand mode: the modifier machinery is inert and the
            # engine's pinch clicks exactly as it always has.
            self._reset_modifier_state()
            observation = (
                max(observations, key=lambda item: item.confidence)
                if observations
                else None
            )
            return self.process(observation, now)

        if len(observations) < 2:
            # Modifier hand absent. If the dominant pinch is holding the
            # button, latch the current mode so a momentary tracking blip
            # cannot drop an in-progress click or drag; the pinch release or
            # the engine's own hand-loss safety ends it. Otherwise fall back
            # to NEUTRAL: pointing works, clicking requires the modifier.
            if not self._pointer_button_down:
                self._set_modifier_mode(_ModifierMode.NEUTRAL)
            observation = (
                max(observations, key=lambda item: item.confidence)
                if observations
                else None
            )
            return self._process_pointer_hand(observation, now)

        pointer_index = self._pointer_hand_index(observations)
        pointer = observations[pointer_index]
        remaining_indices = [
            index for index in range(len(observations)) if index != pointer_index
        ]
        prefer_larger_x = self._dominant_prefers_larger_x(observations)
        modifier_index = max(
            remaining_indices,
            key=lambda index: (
                -_observation_center_x(observations[index])
                if prefer_larger_x
                else _observation_center_x(observations[index]),
                observations[index].confidence,
                -index,
            ),
        )
        modifier_sample = self.recognizer.recognize(observations[modifier_index])
        self._observe_modifier_pose(modifier_sample)
        return self._process_pointer_hand(pointer, now)

    def _process_pointer_hand(
        self,
        observation: HandObservation | None,
        now: float,
    ) -> list[PipelineEvent]:
        """Run the pointer hand with pinch/motion gated by the modifier mode."""
        # NEUTRAL suppresses the dominant pinch (no click without a modifier).
        # Toggling suppress mid-pinch is safe: the engine converts an active
        # PINCH whose request became POINTER into a clean exit with LEFT_UP.
        self.engine.suppress_pinch_click = (
            self._modifier_mode is _ModifierMode.NEUTRAL
        )
        # LOCK freezes the cursor so the click lands exactly where aimed.
        self._freeze_pointer_motion = self._modifier_mode is _ModifierMode.LOCK
        try:
            return self.process(observation, now)
        finally:
            self._freeze_pointer_motion = False

    def _observe_modifier_pose(self, sample: GestureSample | None) -> None:
        """Debounce the modifier hand's pose into a mode transition."""
        target = _ModifierMode.NEUTRAL
        if sample is not None:
            target = _MODIFIER_POSES.get(sample.pose, _ModifierMode.NEUTRAL)
            if target is _ModifierMode.NEUTRAL and all(sample.extended_fingers):
                # The recognizer demotes OPEN_PALM to UNKNOWN when the palm
                # does not face the camera, using the handedness label — which
                # is unreliable (inverted for some users). A mode selector only
                # needs an open hand, so accept all-fingers-extended directly.
                target = _ModifierMode.LOCK
        if target is self._modifier_mode:
            self._modifier_candidate = None
            self._modifier_candidate_frames = 0
            return
        # Latch the active mode while the dominant pinch holds the button so
        # a modifier flicker cannot drop an in-progress click or drag.
        if self._pointer_button_down:
            self._modifier_candidate = None
            self._modifier_candidate_frames = 0
            return
        if target is self._modifier_candidate:
            self._modifier_candidate_frames += 1
        else:
            self._modifier_candidate = target
            self._modifier_candidate_frames = 1
        if self._modifier_candidate_frames >= _MODIFIER_DEBOUNCE_FRAMES:
            self._set_modifier_mode(target)

    def _set_modifier_mode(self, mode: _ModifierMode) -> None:
        self._modifier_mode = mode
        self._modifier_candidate = None
        self._modifier_candidate_frames = 0

    def _reset_modifier_state(self) -> None:
        self._set_modifier_mode(_ModifierMode.NEUTRAL)
        self.engine.suppress_pinch_click = False
        self._freeze_pointer_motion = False

    def _pointer_hand_index(
        self,
        observations: tuple[HandObservation, ...],
    ) -> int:
        prefer_larger_x = self._dominant_prefers_larger_x(observations)

        def dominant_side_key(index: int) -> tuple[float, float, int]:
            observation = observations[index]
            center_x = _observation_center_x(observation)
            side_score = center_x if prefer_larger_x else -center_x
            return side_score, observation.confidence, -index

        return max(range(len(observations)), key=dominant_side_key)

    def _dominant_prefers_larger_x(
        self,
        observations: tuple[HandObservation, ...],
    ) -> bool:
        dominant = str(
            self.settings.get("dominant_hand", DEFAULTS["dominant_hand"])
            if self.settings is not None
            else DEFAULTS["dominant_hand"]
        ).strip().casefold()
        input_is_mirrored = observations[0].input_is_mirrored
        return (dominant == "right") == input_is_mirrored

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
        # The engine's own arm/pause transition releases any held pinch via
        # _exit_active, so no separate button release is needed here.
        self._reset_modifier_state()
        events = self._forced_action_events(self.engine.manual_toggle(now), now)
        events.append(self.status())
        return events

    def force_pause(self, reason: str) -> list[PipelineEvent]:
        now = self._clock()
        if self._clutch is not None:
            self._clutch.set_armed(False, now)
        self._reset_modifier_state()
        events = self._forced_action_events(self.engine.force_pause(reason), now)
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

    def apply_settings(self, settings: dict[str, Any]) -> list[PipelineEvent]:
        previous = self.settings
        click_mode_changed = (
            previous is not None
            and previous.get("click_mode", DEFAULTS["click_mode"])
            != settings.get("click_mode", DEFAULTS["click_mode"])
        )
        release_events: list[PipelineEvent] = []
        if click_mode_changed:
            now = self._clock()
            if self._clutch is not None:
                self._clutch.set_armed(False, now)
            release_events.extend(
                self._forced_action_events(
                    self.engine.force_pause("Paused — click mode changed"),
                    now,
                )
            )
            # force_pause released any held pinch via the engine; the modifier
            # machinery only needs its state cleared.
            self._reset_modifier_state()
        self.gate = ConfidenceGate(
            replace(
                self.gate.thresholds,
                t1_top1=gate_t1(settings["sensitivity"]),
            )
        )
        for field, value in gesture_config_updates(settings).items():
            setattr(self.engine.config, field, value)
        self.controller.pointer_pixels_per_palm = pointer_pixels(
            self._base_pointer_pixels_per_palm,
            settings["cursor_speed"],
        )
        self.engine.suppress_pinch_click = settings["click_mode"] == "two_hand"
        self.settings = dict(settings)
        return release_events

    def _gated_action_events(
        self,
        actions: list[Action],
        now: float,
    ) -> list[PipelineEvent]:
        events: list[PipelineEvent] = []
        for action in actions:
            if action.kind in RELEASING_ACTIONS:
                self._pointer_button_down = False
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

            if (
                action.kind == ActionKind.MOVE_POINTER
                and self._freeze_pointer_motion
            ):
                # LOCK modifier: hold the cursor exactly where it was aimed so
                # the dominant pinch clicks that spot without any drift.
                continue

            if action.kind == ActionKind.LEFT_DOWN:
                self._pointer_button_down = True

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
        max_pixels = self.config.gestures.pointer_max_step_palms * pixels_per_palm
        pixel_magnitude = math.hypot(dx_pixels, dy_pixels)
        if pixel_magnitude > max_pixels:
            scale = max_pixels / pixel_magnitude
            dx_pixels = math.trunc(dx_pixels * scale)
            dy_pixels = math.trunc(dy_pixels * scale)
            # The max-step clamp is intentionally lossy safety behavior; do
            # not retain capped high-speed motion to replay on later frames.
            self._reset_pointer_residual()
        else:
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
            if action.kind == ActionKind.LEFT_DOWN:
                self._pointer_button_down = True
            elif action.kind == ActionKind.LEFT_UP:
                self._pointer_button_down = False
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
