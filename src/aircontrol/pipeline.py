"""UI-agnostic gesture recognition, gating, and dispatch pipeline."""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Callable
from typing import Any

from aircontrol.arena import effective_t1
from aircontrol.buffer import RollingFrameBuffer
from aircontrol.clutch import build_clutch
from aircontrol.config import AppConfig
from aircontrol.controller import ActionController
from aircontrol.density import IncidentalDensity
from aircontrol.domain import Action, ActionKind, GestureSample, HandObservation
from aircontrol.engine import GestureEngine
from aircontrol.gate import ConfidenceGate, GateDecision, heuristic_decision
from aircontrol.ipc import action_event, candidate_event, status_event
from aircontrol.matcher import DtwMatcher, TrajectoryMatcher
from aircontrol.metrics import Metrics
from aircontrol.profile import CalibrationProfile
from aircontrol.recognizer import StaticPoseRecognizer
from aircontrol.segmentation import SegmentationMachine
from aircontrol.store import Store
from aircontrol.trajectory import frame_from_observation
from aircontrol.undo import UndoManager


PipelineEvent = dict[str, Any]
RELEASING_ACTIONS: frozenset[ActionKind] = frozenset({ActionKind.LEFT_UP})
logger = logging.getLogger(__name__)


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
        profile: CalibrationProfile | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.config = config
        self.controller = controller
        self.store = store
        self.metrics = metrics
        self.gate = gate
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
            self.engine = GestureEngine(
                config.gestures,
                clutch=build_clutch(config, profile),
            )
            self._segmentation = SegmentationMachine(profile.motion)
            self._density = self._restore_density(profile.incidental_features)
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
        events = self._forced_action_events(self.engine.manual_toggle(now), now)
        events.append(self.status())
        return events

    def force_pause(self, reason: str) -> list[PipelineEvent]:
        now = self._clock()
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

            events.append(
                self._dispatch_matched(
                    action,
                    confidence=decision.confidence,
                    now=now,
                )
            )
        return events

    def _mapped_action(self, gesture_id: int) -> Action | None:
        if self.store is None:
            return None
        try:
            mapping = self.store.mappings.for_gesture(gesture_id)
            if mapping is None or not mapping.enabled:
                return None
            kind = mapping.action.get("kind")
            amount = mapping.action.get("amount", 0)
            if not isinstance(kind, str):
                raise ValueError("action kind must be a string")
            if type(amount) is not int:
                raise ValueError("action amount must be an integer")
            return Action(ActionKind(kind), amount=amount or 0)
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


__all__ = ["Pipeline", "PipelineEvent"]
