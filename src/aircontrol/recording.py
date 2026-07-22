from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from aircontrol.config import AppConfig
from aircontrol.curation import ConsistencyReport, consistency_report
from aircontrol.density import IncidentalDensity
from aircontrol.matcher import DtwMatcher, MatchResult
from aircontrol.profile import CalibrationProfile
from aircontrol.store import Store
from aircontrol.trajectory import LandmarkFrame, Trajectory


# Safety net only: press-to-start / press-to-stop is the normal path, so a
# take is not expected to run this long. If the user forgets to press stop,
# auto-finalise here rather than capturing indefinitely -- using the exact
# same trim path as an explicit end_take.
MAX_TAKE_SECONDS = 10.0

# Palm-normalised speed math mirrors segmentation.py's SegmentationMachine
# exactly (same center landmarks, same palm-size normalisation) so that a
# recorded take is trimmed by the same notion of "motion" live recognition
# uses -- but here it is applied once, after the fact, to a fixed window
# instead of driving a live onset/offset state machine.
_CENTER_LANDMARKS = (0, 5, 9, 13, 17)
_MINIMUM_PALM_SIZE = 1e-9

# A captured window shorter than this cannot yield a meaningful speed curve.
_MIN_CAPTURED_FRAMES = 2
# Deliberately speed-independent: whatever the peak palm-normalised speed
# turns out to be (a slow gesture and a fast gesture both produce one), the
# motion span is "wherever speed is at least a quarter of its own peak",
# expanded by a small safety margin.
_TRIM_THRESHOLD_RATIO = 0.25
_TRIM_MARGIN_FRAMES = 2
# A peak speed at or below this is indistinguishable from sensor noise on a
# stationary hand -- there was no gesture to trim to.
_MOTION_EPSILON = 1e-6
# Below this, a "trimmed" take is too short to be a usable exemplar; treat it
# the same as no motion at all so the user retries rather than banking noise.
_MIN_TRIMMED_FRAMES = 6

NO_MOTION_REFUSAL = "no motion"


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


def _center(frame: LandmarkFrame) -> tuple[float, float, float]:
    count = len(_CENTER_LANDMARKS)
    return (
        sum(frame.landmarks[index].x for index in _CENTER_LANDMARKS) / count,
        sum(frame.landmarks[index].y for index in _CENTER_LANDMARKS) / count,
        sum(frame.landmarks[index].z for index in _CENTER_LANDMARKS) / count,
    )


def _palm_size(frame: LandmarkFrame) -> float:
    wrist = frame.landmarks[0]
    middle_mcp = frame.landmarks[9]
    return max(
        math.sqrt(
            (middle_mcp.x - wrist.x) ** 2
            + (middle_mcp.y - wrist.y) ** 2
            + (middle_mcp.z - wrist.z) ** 2
        ),
        _MINIMUM_PALM_SIZE,
    )


def _palm_normalized_speeds(frames: Sequence[LandmarkFrame]) -> list[float]:
    """Per-frame palm-normalised speed, frame 0 defined as 0.0."""
    speeds = [0.0]
    for index in range(1, len(frames)):
        previous = frames[index - 1]
        current = frames[index]
        dt = current.timestamp - previous.timestamp
        if dt <= 0.0:
            speeds.append(0.0)
            continue
        displacement = math.dist(_center(current), _center(previous))
        speeds.append(displacement / _palm_size(current) / dt)
    return speeds


def _trim_to_motion(
    frames: Sequence[LandmarkFrame],
) -> tuple[LandmarkFrame, ...] | None:
    """Trim a captured take down to its motion span.

    Speed-independent: the threshold is a fraction of this take's own peak
    speed, so a slow gesture and a fast gesture of the same shape both trim
    to (roughly) their motion, and neither is favoured by a fixed velocity
    floor. Returns ``None`` when the capture holds no discernible motion, or
    when the motion span is too short to be a usable exemplar.
    """
    if len(frames) < _MIN_CAPTURED_FRAMES:
        return None

    speeds = _palm_normalized_speeds(frames)
    peak = max(speeds)
    if peak <= _MOTION_EPSILON:
        return None

    threshold = _TRIM_THRESHOLD_RATIO * peak
    above = [index for index, speed in enumerate(speeds) if speed >= threshold]
    if not above:
        return None

    first = max(0, above[0] - _TRIM_MARGIN_FRAMES)
    last = min(len(frames) - 1, above[-1] + _TRIM_MARGIN_FRAMES)
    trimmed = tuple(frames[first : last + 1])
    if len(trimmed) < _MIN_TRIMMED_FRAMES:
        return None
    return trimmed


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

        self._matcher = DtwMatcher(store)
        self._matcher.refresh()
        self._density = self._restore_density(profile.incidental_features)

        self._confirmed: list[Trajectory] = []
        self._pending: Trajectory | None = None
        self._phase = "capture"
        self._outcome: RecordingOutcome | None = None

        self._capturing = False
        self._capture_started_at: float | None = None
        self._capture_frames: list[LandmarkFrame] = []
        self._capture_state = "idle"
        self._last_take_refused: str | None = None

    def begin_take(self, now: float) -> None:
        """Open an explicit capture window, triggered by the user pressing start.

        Ignored if a take is already pending confirmation, a window is
        already open, recording isn't in the capture phase, or the take
        limit has been reached.
        """
        if self._phase != "capture":
            return
        if self._pending is not None:
            return
        if self._capturing:
            return
        if self.takes_confirmed >= self.rec_config.max_takes:
            return

        self._capturing = True
        self._capture_started_at = now
        self._capture_frames = []
        self._last_take_refused = None
        self._capture_state = "capturing"

    def end_take(self, now: float) -> TakeEvent | None:
        """Close an open capture window, triggered by the user pressing stop.

        A harmless no-op if no window is open (no begin_take, or it was
        already closed). ``now`` is accepted for symmetry with begin_take
        and the safety auto-finalise path; the trim itself only depends on
        frame timestamps already recorded.
        """
        del now
        if self._phase != "capture" or not self._capturing:
            return None
        return self._close_capture_window()

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
        if not self._capturing:
            self._capture_state = "idle"
            return None

        if frame is not None:
            self._capture_frames.append(frame)

        started_at = self._capture_started_at
        if started_at is not None and now - started_at >= MAX_TAKE_SECONDS:
            # Safety net: the user forgot to press stop. Auto-finalise via
            # the exact same trim path as an explicit end_take.
            return self._close_capture_window()
        return None

    def confirm_take(self) -> None:
        if self._phase != "capture" or self._pending is None:
            return
        self._confirmed.append(self._pending)
        self._pending = None
        self._capture_state = "idle"

    def discard_take(self) -> None:
        if self._phase != "capture":
            return
        self._pending = None
        self._capturing = False
        self._capture_frames = []
        self._capture_started_at = None
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

        One of "idle" (waiting for the user to trigger a take), "capturing"
        (an explicit capture window is open), or "pending_take" (a captured
        take is awaiting confirm/discard).
        """
        return self._capture_state

    @property
    def last_take_refused(self) -> str | None:
        """Reason the most recently closed capture window produced no take.

        ``None`` once a fresh take has begun or been captured successfully.
        """
        return self._last_take_refused

    @property
    def capture_started_at(self) -> float | None:
        """Monotonic time the currently open capture window began.

        ``None`` when no window is open; combined with the caller's own
        ``now`` this lets a live UI show an elapsed-time counter.
        """
        return self._capture_started_at

    @property
    def max_take_seconds(self) -> float:
        return MAX_TAKE_SECONDS

    def _close_capture_window(self) -> TakeEvent | None:
        frames = self._capture_frames
        self._capturing = False
        self._capture_frames = []
        self._capture_started_at = None

        trimmed = _trim_to_motion(frames)
        if trimmed is None:
            self._last_take_refused = NO_MOTION_REFUSAL
            self._capture_state = "idle"
            return None

        self._pending = Trajectory(frames=trimmed, handedness=trimmed[0].handedness)
        self._last_take_refused = None
        self._capture_state = "pending_take"
        return TakeEvent(
            take_index=self.takes_confirmed,
            frame_count=len(trimmed),
        )

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
