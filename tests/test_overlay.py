from __future__ import annotations

from dataclasses import replace

import cv2
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


def test_chrome_false_skips_hud_but_still_draws_hand(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    overlay = GestureOverlay()
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    observation = _hand(0.5)
    status = replace(
        _status(),
        armed=False,
        active_pose=Pose.NONE,
        hand_visible=False,
        status_text="DETECTED: NO HAND",
    )

    put_text_calls: list[object] = []
    monkeypatch.setattr(
        cv2, "putText", lambda *args, **kwargs: put_text_calls.append(args)
    )

    hand_draws: list[object] = []
    monkeypatch.setattr(
        overlay, "_draw_hand", lambda *args, **kwargs: hand_draws.append(args)
    )

    overlay.draw(
        frame=frame,
        observation=observation,
        sample=None,
        status=status,
        fps=30.0,
        practice=False,
        chrome=False,
    )

    assert put_text_calls == []
    assert len(hand_draws) == 1
    _, drawn_observation, drawn_armed = hand_draws[0]
    assert drawn_observation is observation
    assert drawn_armed is False


def test_default_chrome_true_matches_prior_hud_behavior(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    overlay = GestureOverlay()
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    observation = _hand(0.5)
    status = _status()

    put_text_calls: list[object] = []
    monkeypatch.setattr(
        cv2, "putText", lambda *args, **kwargs: put_text_calls.append(args)
    )

    overlay.draw(
        frame=frame,
        observation=observation,
        sample=None,
        status=status,
        fps=30.0,
        practice=False,
    )

    assert put_text_calls  # HUD text (AIRCONTROL, DETECTED, etc.) still drawn


def test_chrome_false_returns_frame_byte_identical_to_border_and_hand_only() -> None:
    overlay = GestureOverlay(show_landmarks=False)
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    status = _status()

    rendered = overlay.draw(
        frame=frame,
        observation=None,
        sample=None,
        status=status,
        fps=30.0,
        practice=False,
        chrome=False,
    )

    expected = frame.copy()
    cv2.rectangle(
        expected,
        (22, 22),
        (frame.shape[1] - 22, frame.shape[0] - 22),
        (75, 95, 105),
        1,
        cv2.LINE_AA,
    )
    cv2.line(expected, (40, 42), (90, 42), (255, 205, 65), 2, cv2.LINE_AA)

    assert np.array_equal(rendered, expected)
