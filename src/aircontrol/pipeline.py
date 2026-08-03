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
from aircontrol.ipc import (
    action_event,
    candidate_event,
    gesture_test_event,
    status_event,
)
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
from aircontrol.trajectory import (
    LandmarkFrame,
    Trajectory,
    frame_from_observation,
    frame_shape,
    shape_distance,
)
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
# How long remembered hand positions stay valid for role continuity. Within
# this window a lone hand is matched to the nearer remembered role, so a
# tracking dropout of one hand cannot flip which hand the engine sees.
_ROLE_MEMORY_SECONDS = 1.5
# Two detections closer than this (normalized x) are treated as duplicates of
# one physical hand rather than two hands.
_DUPLICATE_HAND_X = 0.08

# --- Live static-pose recognition -------------------------------------------
#
# A pose gesture fires from a held shape rather than a completed motion
# segment, so it needs its own small state machine running alongside
# SegmentationMachine: watch the gesture hand's recent frames, and once the
# shape has been held (near-still, low-drift) for POSE_DWELL_SECONDS, take a
# single match_pose attempt against the stored pose library.
#
# How long a shape must be held still before it is even considered a
# candidate pose -- long enough that a hand merely passing through a shape on
# its way elsewhere (including the tail of a motion gesture) cannot trip it.
POSE_DWELL_SECONDS = 0.5
# Palm-normalised speed (same units as SegmentationMachine's velocity floor)
# below which the hand counts as "still enough" to accumulate pose dwell.
# Above it, any in-progress dwell is dropped and a latched pose is released:
# a pose and a motion segment must never both fire for the same movement.
_POSE_STILL_SPEED_MAX = 0.15
# Maximum mean shape drift (frame_shape units) tolerated between the frame
# that started the current dwell and any later frame still inside it. A
# bigger drift means the hand changed shape mid-dwell, so the dwell timer
# restarts from that new shape rather than blending two different poses.
_POSE_DRIFT_MAX = 0.06
# Once a pose has fired, the same gesture cannot fire again until its shape
# drifts at least this far from the shape it fired on -- i.e. the user
# visibly released or changed the pose. Deliberately looser than
# _POSE_DRIFT_MAX so ordinary micro-jitter while holding a fired pose still
# does not read as "released".
_POSE_RELEASE_DRIFT = 0.12
# Bound on how many frames a dwell window keeps, so an unusually long hold
# cannot grow the DTW comparison unboundedly.
_POSE_MAX_DWELL_FRAMES = 90
_MINIMUM_PALM_SIZE = 1e-9

# A custom gesture/pose must clear this DTW-similarity floor to fire, no
# matter how permissive the sensitivity slider's gate_t1 mapping gets: a
# recognized shape must be at least 90% similar to the stored exemplar. This
# sits alongside (not instead of) the ConfidenceGate/t1_offset checks below --
# it is a hard lower bound the sensitivity setting can never relax.
MIN_CUSTOM_CONFIDENCE = 0.90


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
        self._last_pointer_x: float | None = None
        self._last_modifier_x: float | None = None
        self._hand_positions_at: float | None = None
        self._pointer_residual_x_pixels = 0.0
        self._pointer_residual_y_pixels = 0.0
        self._pointer_residual_pose = Pose.NONE
        if settings is not None:
            self.apply_settings(settings)
        self.recognizer = StaticPoseRecognizer(config.gestures)
        self.last_sample: GestureSample | None = None
        self._undo = UndoManager(metrics, clock=clock)
        self._released = False

        # Live pose dwell/latch/release state; see the POSE_* constants above.
        self._pose_prev_frame: LandmarkFrame | None = None
        self._pose_dwell_start: float | None = None
        self._pose_reference_shape: Any | None = None
        self._pose_dwell_frames: list[LandmarkFrame] = []
        self._pose_latched_gesture_id: int | None = None
        self._pose_latched_shape: Any | None = None

        # Gesture-test mode: see start_gesture_test/stop_gesture_test. While
        # active, recognition runs as if armed but never dispatches -- a
        # gesture_test event describes each attempt instead.
        self.gesture_test_id: int | None = None
        self.gesture_test_kind: str | None = None
        self._gesture_test_last_state: str | None = None

    def process(
        self,
        observation: HandObservation | None,
        now: float,
        *,
        recognize_gestures: bool = True,
    ) -> list[PipelineEvent]:
        """Process one observation and return v1 events for its resulting state.

        ``recognize_gestures`` is False when a caller (two-hand mode) drives
        custom-gesture recognition itself, from a hand chosen independently of
        the pointer/modifier routing. See :meth:`_recognize_gesture`.
        """
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
        if recognize_gestures:
            events.extend(self._recognize_gesture(frame, now))
        if self.config.metrics.cpu_sampling:
            self.metrics.sample_cpu()
        events.append(self.status())
        return events

    def _recognize_gesture(
        self,
        frame: LandmarkFrame | None,
        now: float,
    ) -> list[PipelineEvent]:
        """Segment and match a custom gesture from one gesture-hand frame.

        Kept independent of the pointer/modifier routing: in two-hand mode a
        lone hand flips between the pointer and modifier roles as it crosses
        the frame, and feeding those role gaps to the segmenter as ``None``
        fragmented the trajectory so a recorded gesture never matched.
        """
        events: list[PipelineEvent] = []
        if self._segmentation is None:
            return events
        testing = self._gesture_test_active
        segment = self._segmentation.update(
            frame, self.engine.armed or testing, now
        )
        events.extend(self._recognize_pose(frame, now))
        if segment is None:
            if testing and frame is None:
                events.extend(self._gesture_test_status_event("no_hand", now))
            return events

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
        has_match_library = result is not None and bool(result.scores)
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
        if decision.fire and top1 < MIN_CUSTOM_CONFIDENCE:
            decision = GateDecision(
                fire=False,
                confidence=decision.confidence,
                reason="below_min_confidence",
            )
        matched_gesture_id = (
            result.gesture_id if result is not None and has_match_library else None
        )
        if testing:
            events.append(
                self._gesture_test_attempt_event(
                    matched_gesture_id=matched_gesture_id,
                    top1=top1,
                    top2=top2,
                    decision=decision,
                    now=now,
                )
            )
            return events
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
        return events

    def _recognize_pose(
        self,
        frame: LandmarkFrame | None,
        now: float,
    ) -> list[PipelineEvent]:
        """Fire a custom pose gesture from a held, near-still hand shape.

        Runs alongside :meth:`_recognize_gesture`'s motion segmentation, on
        the same gesture-hand frame, so it covers single- and two-hand mode
        identically. A pose only ever fires once per hold: once latched, it
        will not fire again until the shape visibly changes or the hand
        moves/leaves (see the POSE_* module constants).
        """
        testing = self._gesture_test_active
        if self.matcher is None or not (self.engine.armed or testing) or frame is None:
            self._reset_pose_dwell()
            self._pose_latched_gesture_id = None
            self._pose_prev_frame = None
            return []

        speed = self._pose_speed(frame)
        self._pose_prev_frame = frame
        if speed > _POSE_STILL_SPEED_MAX:
            # The hand is moving: this is motion-segment territory, not a
            # held pose. Drop any in-progress dwell and release the latch so
            # a subsequent hold (even of the same shape) can fire again.
            self._reset_pose_dwell()
            self._pose_latched_gesture_id = None
            if testing:
                return self._gesture_test_status_event("moving", now)
            return []

        shape = frame_shape(frame)

        if self._pose_latched_gesture_id is not None:
            assert self._pose_latched_shape is not None
            if shape_distance(shape, self._pose_latched_shape) > _POSE_RELEASE_DRIFT:
                self._pose_latched_gesture_id = None
                self._reset_pose_dwell()
            else:
                if testing:
                    return self._gesture_test_status_event("holding", now)
                return []

        if (
            self._pose_dwell_start is None
            or self._pose_reference_shape is None
            or shape_distance(shape, self._pose_reference_shape) > _POSE_DRIFT_MAX
        ):
            self._pose_dwell_start = now
            self._pose_reference_shape = shape
            self._pose_dwell_frames = [frame]
            if testing:
                return self._gesture_test_status_event("holding", now)
            return []

        self._pose_dwell_frames.append(frame)
        if len(self._pose_dwell_frames) > _POSE_MAX_DWELL_FRAMES:
            self._pose_dwell_frames = self._pose_dwell_frames[-_POSE_MAX_DWELL_FRAMES:]
        if now - self._pose_dwell_start < POSE_DWELL_SECONDS:
            if testing:
                return self._gesture_test_status_event("holding", now)
            return []

        events = self._attempt_pose_match(shape, now)
        # Throttle to one attempt per dwell period, whether it fired or not:
        # a fresh dwell starts immediately on the next still frame.
        self._reset_pose_dwell()
        return events

    def _attempt_pose_match(
        self,
        shape: Any,
        now: float,
    ) -> list[PipelineEvent]:
        events: list[PipelineEvent] = []
        assert self.matcher is not None
        trajectory = Trajectory(
            frames=tuple(self._pose_dwell_frames),
            handedness=self._pose_dwell_frames[-1].handedness,
        )
        self.metrics.note_candidate(armed=self.engine.armed)
        result = self.matcher.match_pose(trajectory)
        has_match_library = bool(result.scores)
        if has_match_library:
            top1 = result.top1
            top2 = result.top2
            self.metrics.begin_gesture()
        else:
            top1 = 1.0
            top2 = 0.0
        decision = self.gate.evaluate(top1=top1, top2=top2, incidental_distance=math.inf)
        if (
            decision.fire
            and has_match_library
            and result.gesture_id is not None
        ):
            offset = self._threshold_offset(result.gesture_id)
            if offset > 0.0 and top1 < effective_t1(self.gate.thresholds, offset):
                decision = GateDecision(
                    fire=False,
                    confidence=decision.confidence,
                    reason="t1_offset",
                )
        if decision.fire and top1 < MIN_CUSTOM_CONFIDENCE:
            decision = GateDecision(
                fire=False,
                confidence=decision.confidence,
                reason="below_min_confidence",
            )
        matched_gesture_id = result.gesture_id if has_match_library else None
        if self._gesture_test_active:
            events.append(
                self._gesture_test_attempt_event(
                    matched_gesture_id=matched_gesture_id,
                    top1=top1,
                    top2=top2,
                    decision=decision,
                    now=now,
                )
            )
            # Deliberately do not latch while testing: the user holds the
            # pose several times, and each hold must be free to attempt
            # again once dwell re-accumulates (see start_gesture_test).
            return events
        events.append(
            candidate_event(
                gate="fire" if decision.fire else "abstain",
                reason=decision.reason,
                confidence=decision.confidence,
                ts=now,
            )
        )
        if has_match_library and decision.fire and result.gesture_id is not None:
            action = self._mapped_action(result.gesture_id)
            if action is not None:
                events.append(
                    self._dispatch_matched(
                        action,
                        confidence=decision.confidence,
                        now=now,
                    )
                )
                self._pose_latched_gesture_id = result.gesture_id
                self._pose_latched_shape = shape
        return events

    @staticmethod
    def _pose_center(frame: LandmarkFrame) -> tuple[float, float, float]:
        anchors = (0, 5, 9, 13, 17)
        return (
            sum(frame.landmarks[index].x for index in anchors) / len(anchors),
            sum(frame.landmarks[index].y for index in anchors) / len(anchors),
            sum(frame.landmarks[index].z for index in anchors) / len(anchors),
        )

    def _pose_speed(self, frame: LandmarkFrame) -> float:
        """Palm-normalised hand-center speed, mirroring SegmentationMachine."""
        previous = self._pose_prev_frame
        if previous is None:
            return 0.0
        dt = frame.timestamp - previous.timestamp
        if dt <= 0.0:
            return 0.0
        displacement = math.dist(
            self._pose_center(frame), self._pose_center(previous)
        )
        wrist = frame.landmarks[0]
        middle_mcp = frame.landmarks[9]
        palm_size = max(
            math.sqrt(
                (middle_mcp.x - wrist.x) ** 2
                + (middle_mcp.y - wrist.y) ** 2
                + (middle_mcp.z - wrist.z) ** 2
            ),
            _MINIMUM_PALM_SIZE,
        )
        return displacement / palm_size / dt

    def _reset_pose_dwell(self) -> None:
        self._pose_dwell_start = None
        self._pose_reference_shape = None
        self._pose_dwell_frames = []

    @property
    def _gesture_test_active(self) -> bool:
        return self.gesture_test_id is not None

    def start_gesture_test(self, gesture_id: int, kind: str) -> None:
        """Enter gesture-test mode for ``gesture_id``.

        While active, ``_recognize_gesture``/``_recognize_pose`` run
        recognition as if the engine were armed -- covering the common case
        where a test starts right after a recording, while the engine is
        still paused/disarmed -- but never call ``_mapped_action`` or
        ``_dispatch_matched``: a ``gesture_test`` event is emitted for each
        attempt instead. Safe to call repeatedly; always resets prior test
        and pose dwell/latch state so the first attempt is judged fresh.
        """
        self.gesture_test_id = gesture_id
        self.gesture_test_kind = kind if kind in ("motion", "pose") else "motion"
        self._gesture_test_last_state = None
        self._reset_pose_dwell()
        self._pose_latched_gesture_id = None
        self._pose_latched_shape = None
        self._pose_prev_frame = None

    def stop_gesture_test(self) -> None:
        """Leave gesture-test mode, restoring normal recognition/dispatch.

        Idempotent -- safe to call when no test is active.
        """
        self.gesture_test_id = None
        self.gesture_test_kind = None
        self._gesture_test_last_state = None

    def _gesture_test_status_event(
        self,
        state: str,
        now: float,
    ) -> list[PipelineEvent]:
        """Emit a live-status gesture_test event, but only on change."""
        if state == self._gesture_test_last_state:
            return []
        self._gesture_test_last_state = state
        return [
            gesture_test_event(
                state=state,
                min_confidence=MIN_CUSTOM_CONFIDENCE,
                ts=now,
            )
        ]

    def _gesture_test_attempt_event(
        self,
        *,
        matched_gesture_id: int | None,
        top1: float,
        top2: float,
        decision: GateDecision,
        now: float,
    ) -> PipelineEvent:
        """Build the gesture_test event for one match attempt.

        Attempts always broadcast (unlike the throttled live-status events):
        the UI's "recognized N of 2" counter needs to see every one.
        """
        fired = bool(decision.fire and matched_gesture_id is not None)
        self._gesture_test_last_state = "attempt"
        return gesture_test_event(
            state="attempt",
            matched_gesture_id=matched_gesture_id,
            confidence=top1,
            runner_up=top2,
            fired=fired,
            is_target=matched_gesture_id == self.gesture_test_id,
            reason=decision.reason,
            min_confidence=MIN_CUSTOM_CONFIDENCE,
            ts=now,
        )

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

        # Collapse duplicate detections of one physical hand: MediaPipe can
        # briefly report the same hand twice, and treating the duplicate as
        # the "other" hand would hand a fist to the engine (false disarm).
        if len(observations) >= 2:
            xs = [_observation_center_x(observation) for observation in observations]
            if max(xs) - min(xs) < _DUPLICATE_HAND_X:
                observations = (
                    max(observations, key=lambda item: item.confidence),
                )

        if len(observations) < 2:
            observation = (
                max(observations, key=lambda item: item.confidence)
                if observations
                else None
            )
            if observation is not None and self._lone_hand_is_modifier(
                observation, now
            ):
                # The visible hand is the MODIFIER: keep driving the mode from
                # it (a held fist keeps drag mode alive) and never feed it to
                # the engine — so a modifier fist can never disarm the system.
                self._remember_positions(
                    modifier_x=_observation_center_x(observation), now=now
                )
                self._observe_modifier_pose(self.recognizer.recognize(observation))
                # The engine still gets nothing, but the lone hand is the only
                # gesture hand there is: recognize custom gestures from it.
                return self._with_gesture_events(
                    self._process_pointer_hand(
                        None, now, recognize_gestures=False
                    ),
                    observation,
                    now,
                )
            # The visible hand is the POINTER (or nothing is visible). If the
            # dominant pinch is holding the button, latch the current mode so
            # a modifier dropout cannot drop an in-progress click or drag.
            if observation is not None:
                self._remember_positions(
                    pointer_x=_observation_center_x(observation), now=now
                )
            if not self._pointer_button_down:
                self._set_modifier_mode(_ModifierMode.NEUTRAL)
            return self._with_gesture_events(
                self._process_pointer_hand(
                    observation, now, recognize_gestures=False
                ),
                observation,
                now,
            )

        pointer_index = self._assign_pointer_index(observations, now)
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
        self._remember_positions(
            pointer_x=_observation_center_x(pointer),
            modifier_x=_observation_center_x(observations[modifier_index]),
            now=now,
        )
        modifier_sample = self.recognizer.recognize(observations[modifier_index])
        self._observe_modifier_pose(modifier_sample)
        # With both hands up the dominant (pointer) hand is the gesture hand.
        return self._with_gesture_events(
            self._process_pointer_hand(pointer, now, recognize_gestures=False),
            pointer,
            now,
        )

    def _with_gesture_events(
        self,
        control_events: list[PipelineEvent],
        gesture_observation: HandObservation | None,
        now: float,
    ) -> list[PipelineEvent]:
        """Fold custom-gesture events into two-hand control events.

        Recognition runs exactly once per frame, on a gesture hand picked
        independently of the pointer/modifier split, and the trailing status
        event stays last so consumers keep seeing state after the actions.
        """
        gesture_frame = (
            frame_from_observation(gesture_observation, now)
            if gesture_observation is not None
            else None
        )
        gesture_events = self._recognize_gesture(gesture_frame, now)
        if not gesture_events:
            return control_events
        if control_events and control_events[-1].get("type") == "status":
            return [*control_events[:-1], *gesture_events, control_events[-1]]
        return [*control_events, *gesture_events]

    def _remember_positions(
        self,
        *,
        pointer_x: float | None = None,
        modifier_x: float | None = None,
        now: float,
    ) -> None:
        if pointer_x is not None:
            self._last_pointer_x = pointer_x
        if modifier_x is not None:
            self._last_modifier_x = modifier_x
        self._hand_positions_at = now

    def _role_memory_fresh(self, now: float) -> bool:
        return (
            self._hand_positions_at is not None
            and now - self._hand_positions_at <= _ROLE_MEMORY_SECONDS
        )

    def _lone_hand_is_modifier(
        self,
        observation: HandObservation,
        now: float,
    ) -> bool:
        """Classify a lone hand as modifier (True) or pointer (False)."""
        x = _observation_center_x(observation)
        if (
            self._role_memory_fresh(now)
            and self._last_pointer_x is not None
            and self._last_modifier_x is not None
        ):
            # Continuity: match the hand to the nearer remembered role so a
            # dropout of the other hand cannot flip which hand is which.
            return abs(x - self._last_modifier_x) < abs(x - self._last_pointer_x)
        # No fresh memory: fall back to which half of the frame the hand is
        # in. The dominant/pointer hand lives on the dominant side.
        prefer_larger_x = self._dominant_prefers_larger_x((observation,))
        on_dominant_side = x >= 0.5 if prefer_larger_x else x <= 0.5
        return not on_dominant_side

    def _assign_pointer_index(
        self,
        observations: tuple[HandObservation, ...],
        now: float,
    ) -> int:
        """Pick the pointer hand, preferring continuity over raw side."""
        if (
            self._role_memory_fresh(now)
            and self._last_pointer_x is not None
            and self._last_modifier_x is not None
        ):
            def continuity_cost(pointer_index: int) -> float:
                other = next(
                    index
                    for index in range(len(observations))
                    if index != pointer_index
                )
                assert self._last_pointer_x is not None
                assert self._last_modifier_x is not None
                return abs(
                    _observation_center_x(observations[pointer_index])
                    - self._last_pointer_x
                ) + abs(
                    _observation_center_x(observations[other])
                    - self._last_modifier_x
                )

            return min(range(len(observations)), key=continuity_cost)
        return self._pointer_hand_index(observations)

    def _process_pointer_hand(
        self,
        observation: HandObservation | None,
        now: float,
        *,
        recognize_gestures: bool = True,
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
            return self.process(
                observation,
                now,
                recognize_gestures=recognize_gestures,
            )
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


__all__ = [
    "MIN_CUSTOM_CONFIDENCE",
    "POSE_DWELL_SECONDS",
    "Pipeline",
    "PipelineEvent",
    "action_category",
]
