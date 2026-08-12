from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from aircontrol.dtw import dtw_distance
from aircontrol.store import Store
from aircontrol.trajectory import NormalizedTrajectory, Trajectory, normalize


@dataclass(frozen=True, slots=True)
class MatchResult:
    gesture_id: int | None
    top1: float
    top2: float
    scores: dict[int, float]


class TrajectoryMatcher(Protocol):
    def match(self, traj: Trajectory) -> MatchResult: ...

    def match_pose(self, traj: Trajectory) -> MatchResult: ...

    def refresh(self) -> None: ...


class DtwMatcher:
    """DTW-nearest-exemplar matcher, kept partitioned by gesture kind.

    Motion gestures (recorded from a moving take, fired from a completed
    ``SegmentationMachine`` segment) and pose gestures (recorded from a held
    hand shape, fired from a live dwell window) share the same DTW/normalize
    machinery, but must never cross-fire: a held pose is not a motion
    segment and a motion segment's incidental stillness at its edges is not
    a held pose. :meth:`match` only ever considers motion gestures;
    :meth:`match_pose` only ever considers pose gestures.
    """

    def __init__(
        self,
        store: Store,
        *,
        resample_length: int = 45,
        band: int = 8,
        velocity_weight: float = 0.3,
        # Weight on the engineered hand-detail features (palm facing, finger
        # spread/fold, thumb). Tuned by measurement: at 1.0 a correct held
        # POSE scored ~0.86 -- below pipeline.MIN_CUSTOM_CONFIDENCE -- because
        # a static pose has no trajectory to carry the match, so the features
        # dominate. 0.25 keeps a correct pose comfortably above the firing
        # floor while a different pose (~0.44) and the same shape facing away
        # (~0.72) fall well below it (see tests/test_matcher.py for a fresh
        # measurement against the current floor).
        feature_weight: float = 0.25,
        max_exemplars: int = 15,
    ) -> None:
        self._store = store
        self._resample_length = resample_length
        self._band = band
        self._velocity_weight = velocity_weight
        self._feature_weight = feature_weight
        self._max_exemplars = max_exemplars
        self._exemplars: dict[int, tuple[NormalizedTrajectory, ...]] = {}
        self._kinds: dict[int, str] = {}

    def refresh(self) -> None:
        exemplars: dict[int, tuple[NormalizedTrajectory, ...]] = {}
        kinds: dict[int, str] = {}
        for gesture in self._store.gestures.list():
            stored = self._store.exemplars.list(gesture.id)
            recent = stored[-self._max_exemplars :] if self._max_exemplars > 0 else []
            exemplars[gesture.id] = tuple(
                normalize(trajectory, resample_length=self._resample_length)
                for trajectory in recent
            )
            kinds[gesture.id] = gesture.kind
        self._exemplars = exemplars
        self._kinds = kinds

    def match(self, traj: Trajectory) -> MatchResult:
        return self._match(traj, kind="motion")

    def match_pose(self, traj: Trajectory) -> MatchResult:
        return self._match(traj, kind="pose")

    def _match(self, traj: Trajectory, *, kind: str) -> MatchResult:
        candidates = {
            gesture_id: gesture_exemplars
            for gesture_id, gesture_exemplars in self._exemplars.items()
            if self._kinds.get(gesture_id, "motion") == kind
        }
        if not candidates:
            return MatchResult(None, 0.0, 0.0, {})

        normalized = normalize(traj, resample_length=self._resample_length)
        scores = {
            gesture_id: max(
                (
                    1.0
                    / (
                        1.0
                        + dtw_distance(
                            normalized.canonical,
                            exemplar.canonical,
                            band=self._band,
                            velocity_weight=self._velocity_weight,
                            features_a=normalized.features,
                            features_b=exemplar.features,
                            feature_weight=self._feature_weight,
                        )
                    )
                    for exemplar in gesture_exemplars
                ),
                default=0.0,
            )
            for gesture_id, gesture_exemplars in candidates.items()
        }
        ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
        gesture_id, top1 = ranked[0]
        top2 = ranked[1][1] if len(ranked) > 1 else 0.0
        return MatchResult(
            gesture_id=gesture_id,
            top1=top1,
            top2=top2,
            scores=scores,
        )
