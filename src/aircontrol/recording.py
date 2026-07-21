from __future__ import annotations

from dataclasses import dataclass

from aircontrol.config import AppConfig
from aircontrol.curation import ConsistencyReport, consistency_report
from aircontrol.density import IncidentalDensity
from aircontrol.matcher import DtwMatcher, MatchResult
from aircontrol.profile import CalibrationProfile
from aircontrol.segmentation import CandidateSegment, SegmentationMachine
from aircontrol.store import Store
from aircontrol.trajectory import LandmarkFrame, Trajectory


@dataclass(frozen=True, slots=True)
class RecordingConfig:
    min_takes: int = 8
    max_takes: int = 12
    consistency_max_mean: float = 0.35
    confusability_min_margin: float = 0.15
    incidental_min_distance: float = 1.0


@dataclass(frozen=True, slots=True)
class TakeEvent:
    take_index: int
    frame_count: int


@dataclass(frozen=True, slots=True)
class RecordingOutcome:
    saved: bool
    reason: str
    gesture_id: int | None
    consistency: ConsistencyReport | None
    conflict_gesture_id: int | None


class RecordingSession:
    def __init__(
        self,
        name: str,
        store: Store,
        profile: CalibrationProfile,
        config: AppConfig,
        rec_config: RecordingConfig = RecordingConfig(),
    ) -> None:
        self.name = name
        self.store = store
        self.profile = profile
        self.config = config
        self.rec_config = rec_config

        self._segmentation = SegmentationMachine(profile.motion)
        self._matcher = DtwMatcher(store)
        self._matcher.refresh()
        self._density = self._restore_density(profile.incidental_features)

        self._confirmed: list[Trajectory] = []
        self._pending: CandidateSegment | None = None
        self._phase = "capture"
        self._outcome: RecordingOutcome | None = None
        self._capture_state = "idle"

    def feed(
        self,
        frame: LandmarkFrame | None,
        now: float,
    ) -> TakeEvent | None:
        if (
            self._phase != "capture"
            or self.takes_confirmed >= self.rec_config.max_takes
        ):
            return None
        if self._pending is not None:
            self._capture_state = "pending_take"
            return None

        segment = self._segmentation.update(frame, armed=True, now=now)
        self._capture_state = self._derive_capture_state(frame, segment)
        if segment is None:
            return None

        self._pending = segment
        return TakeEvent(
            take_index=self.takes_confirmed,
            frame_count=len(segment.trajectory.frames),
        )

    def confirm_take(self) -> None:
        if self._phase != "capture" or self._pending is None:
            return
        self._confirmed.append(self._pending.trajectory)
        self._pending = None
        self._capture_state = "idle"

    def discard_take(self) -> None:
        if self._phase != "capture":
            return
        self._pending = None
        self._capture_state = "idle"

    @property
    def takes_confirmed(self) -> int:
        return len(self._confirmed)

    @property
    def phase(self) -> str:
        return self._phase

    @property
    def capture_state(self) -> str:
        """Live capture feedback for the recording UI.

        One of "idle" (no frame fed yet), "searching" (no hand seen and the
        segmentation machine has nothing to preserve), "hand_present" (a hand
        is tracked but not moving), "in_motion" (a gesture is being traced),
        or "pending_take" (a candidate segment is awaiting confirm/discard).
        """
        return self._capture_state

    def _derive_capture_state(
        self,
        frame: LandmarkFrame | None,
        segment: CandidateSegment | None,
    ) -> str:
        if segment is not None:
            return "pending_take"

        phase = self._segmentation.state_name
        if phase == "in_gesture":
            return "in_motion"
        if phase == "present":
            return "hand_present"
        # phase == "waiting": either a hand was just seen for the first time
        # (still debouncing presence) or no hand has been seen at all.
        return "hand_present" if frame is not None else "searching"

    def finish(self) -> RecordingOutcome:
        if self._outcome is not None:
            return self._outcome

        if self.takes_confirmed < self.rec_config.min_takes:
            return self._finish_refused("need more takes")

        report = consistency_report(self._confirmed)
        if report.mean_distance > self.rec_config.consistency_max_mean:
            return self._finish_refused("inconsistent", consistency=report)

        conflict = self._most_confusable_match()
        if (
            conflict is not None
            and conflict.top1 >= 1.0 - self.rec_config.confusability_min_margin
        ):
            return self._finish_refused(
                "too similar",
                consistency=report,
                conflict_gesture_id=conflict.gesture_id,
            )

        if self._resembles_incidental_motion():
            return self._finish_refused(
                "resembles desk motion",
                consistency=report,
            )

        gesture = self.store.gestures.add(self.name)
        for take in self._confirmed:
            self.store.exemplars.add(gesture.id, take)
        self.store.gesture_stats.get(gesture.id)

        outcome = RecordingOutcome(
            saved=True,
            reason="saved",
            gesture_id=gesture.id,
            consistency=report,
            conflict_gesture_id=None,
        )
        self._phase = "saved"
        self._outcome = outcome
        return outcome

    def _most_confusable_match(self) -> MatchResult | None:
        conflict: MatchResult | None = None
        for take in self._confirmed:
            result = self._matcher.match(take)
            if result.gesture_id is None:
                continue
            if conflict is None or result.top1 > conflict.top1:
                conflict = result
        return conflict

    def _resembles_incidental_motion(self) -> bool:
        if self._density is None:
            return False
        close_take_count = sum(
            self._density.distance(take) < self.rec_config.incidental_min_distance
            for take in self._confirmed
        )
        return close_take_count * 2 > self.takes_confirmed

    def _finish_refused(
        self,
        reason: str,
        *,
        consistency: ConsistencyReport | None = None,
        conflict_gesture_id: int | None = None,
    ) -> RecordingOutcome:
        outcome = RecordingOutcome(
            saved=False,
            reason=reason,
            gesture_id=None,
            consistency=consistency,
            conflict_gesture_id=conflict_gesture_id,
        )
        self._phase = "refused"
        self._outcome = outcome
        return outcome

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
