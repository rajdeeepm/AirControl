from __future__ import annotations

import numpy as np
import pytest

from aircontrol.domain import EngineStatus, HandObservation, Point3D, Pose
from aircontrol.overlay import GestureOverlay


def _hand(x: float) -> HandObservation:
    return HandObservation(
        landmarks=tuple(Point3D(x, 0.5) for _ in range(21)),
    )


def _status() -> EngineStatus:
    return EngineStatus(
        armed=True,
        raw_pose=Pose.POINTER,
        active_pose=Pose.POINTER,
        hold_progress=0.0,
        hand_visible=True,
        status_text="Tracking",
    )


def test_draw_renders_every_provided_observation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    overlay = GestureOverlay()
    primary = _hand(0.35)
    secondary = _hand(0.65)
    draws: list[tuple[HandObservation, bool]] = []

    def record_draw(
        _frame: np.ndarray,
        observation: HandObservation,
        armed: bool,
    ) -> None:
        draws.append((observation, armed))

    monkeypatch.setattr(overlay, "_draw_hand", record_draw)

    overlay.draw(
        frame=np.zeros((480, 640, 3), dtype=np.uint8),
        observation=primary,
        sample=None,
        status=_status(),
        fps=30.0,
        practice=True,
        observations=(primary, secondary),
    )

    assert draws == [(primary, True), (secondary, True)]


def test_single_observation_collection_matches_legacy_render() -> None:
    overlay = GestureOverlay()
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    observation = _hand(0.5)
    status = _status()

    legacy = overlay.draw(frame, observation, None, status, 30.0, True)
    with_collection = overlay.draw(
        frame,
        observation,
        None,
        status,
        30.0,
        True,
        observations=(observation,),
    )

    assert np.array_equal(with_collection, legacy)
