from __future__ import annotations

import json
import logging
import math

import pytest

from aircontrol.config import AppConfig, ClutchConfig
from aircontrol.controller import ActionController
from aircontrol.daemon import Daemon
from aircontrol.domain import HandObservation, Point3D
from aircontrol.gate import ConfidenceGate, GateThresholds
from aircontrol.input_sink import VK_ALT, VK_SHIFT, VK_TAB
from aircontrol.ipc import candidate_event, parse_command
from aircontrol.metrics import Metrics
from aircontrol.pipeline import Pipeline
from aircontrol.profile import (
    CalibrationProfile,
    InteractionVolume,
    LightingProfile,
    MotionSignature,
    save_profile,
)
from aircontrol.store import Store
from aircontrol.trajectory import LandmarkFrame, Trajectory


_ANCHOR_INDICES = frozenset((0, 5, 9, 17))
_PRESENCE_FRAMES = 5
_MOTION_FRAMES = 10
_OFFSET_FRAMES = 4
_FRAME_INTERVAL = 0.05
_SCRIPT_END = (
    _PRESENCE_FRAMES + _MOTION_FRAMES + _OFFSET_FRAMES - 1
) * _FRAME_INTERVAL


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _configured_app() -> AppConfig:
    config = AppConfig.defaults()
    config.clutch = ClutchConfig(
        mode="always_on",
        acknowledged_expert_mode=True,
    )
    config.gestures.stability_seconds = 100.0
    config.gestures.max_observation_gap_seconds = 1.0
    config.metrics.cpu_sampling = False
    return config


def _profile() -> CalibrationProfile:
    return CalibrationProfile(
        hand_size=1.0,
        volume=InteractionVolume(-2.0, 2.0, -2.0, 2.0),
        motion=MotionSignature(velocity_floor=0.15, velocity_ceiling=5.0),
        lighting=LightingProfile(120.0, 0.01, True),
        incidental_features=(),
        created_at=1.0,
    )


def _strict_gate() -> ConfidenceGate:
    return ConfidenceGate(
        GateThresholds(
            t1_top1=0.9,
            t2_margin=0.1,
            t3_incidental=0.0,
        )
    )


def _base_landmarks() -> list[Point3D]:
    landmarks = [
        Point3D(
            x=(index % 5) * 0.08,
            y=(index // 5) * 0.12,
            z=(index % 3) * 0.02,
        )
        for index in range(21)
    ]
    landmarks[0] = Point3D(0.0, 0.0, 0.0)
    landmarks[5] = Point3D(1.0, 0.0, 0.0)
    landmarks[9] = Point3D(0.0, 1.0, 0.0)
    landmarks[17] = Point3D(0.0, 0.8, 0.0)
    return landmarks


def _motion_offsets(kind: str, progress: float, amplitude: float) -> tuple[float, float]:
    if kind == "horizontal":
        return amplitude * progress, 0.0
    if kind == "vertical":
        return 0.0, amplitude * progress
    if kind == "circle":
        angle = 2.0 * math.pi * progress
        return (
            amplitude * (math.cos(angle) - 1.0),
            amplitude * math.sin(angle),
        )
    raise ValueError(f"Unknown motion kind: {kind}")


def _motion_landmarks(
    kind: str,
    progress: float,
    *,
    amplitude: float,
    noise: float,
    phase: float,
    frame_index: int,
) -> tuple[Point3D, ...]:
    x_offset, y_offset = _motion_offsets(kind, progress, amplitude)
    global_x = 0.25 * progress
    landmarks = _base_landmarks()
    for index, point in enumerate(landmarks):
        internal_x = 0.0
        internal_y = 0.0
        if index not in _ANCHOR_INDICES:
            angle = phase + frame_index * 0.73 + index * 0.19
            internal_x = x_offset + noise * math.sin(angle)
            internal_y = y_offset + noise * math.cos(angle)
        landmarks[index] = Point3D(
            x=point.x + global_x + internal_x,
            y=point.y + internal_y,
            z=point.z,
        )
    return tuple(landmarks)


def _observation(
    kind: str,
    progress: float,
    *,
    amplitude: float,
    noise: float,
    phase: float,
    frame_index: int,
) -> HandObservation:
    return HandObservation(
        landmarks=_motion_landmarks(
            kind,
            progress,
            amplitude=amplitude,
            noise=noise,
            phase=phase,
            frame_index=frame_index,
        ),
        handedness="Right",
        confidence=0.99,
        image_width=1,
        image_height=1,
    )


def _scripted_observations(
    kind: str,
    *,
    amplitude: float = 1.03,
    noise: float = 0.009,
    phase: float = 0.23,
) -> list[tuple[float, HandObservation]]:
    scripted: list[tuple[float, HandObservation]] = []
    for frame_index in range(_PRESENCE_FRAMES):
        scripted.append(
            (
                frame_index * _FRAME_INTERVAL,
                _observation(
                    kind,
                    0.0,
                    amplitude=amplitude,
                    noise=0.0,
                    phase=phase,
                    frame_index=frame_index,
                ),
            )
        )

    for motion_index in range(_MOTION_FRAMES):
        frame_index = _PRESENCE_FRAMES + motion_index
        scripted.append(
            (
                frame_index * _FRAME_INTERVAL,
                _observation(
                    kind,
                    (motion_index + 1) / _MOTION_FRAMES,
                    amplitude=amplitude,
                    noise=noise,
                    phase=phase,
                    frame_index=motion_index,
                ),
            )
        )

    for offset_index in range(_OFFSET_FRAMES):
        frame_index = _PRESENCE_FRAMES + _MOTION_FRAMES + offset_index
        scripted.append(
            (
                frame_index * _FRAME_INTERVAL,
                _observation(
                    kind,
                    1.0,
                    amplitude=amplitude,
                    noise=noise,
                    phase=phase,
                    frame_index=_MOTION_FRAMES - 1,
                ),
            )
        )
    return scripted


def _candidate_trajectory(
    kind: str,
    *,
    amplitude: float = 1.03,
    noise: float = 0.009,
    phase: float = 0.23,
) -> Trajectory:
    scripted = _scripted_observations(
        kind,
        amplitude=amplitude,
        noise=noise,
        phase=phase,
    )
    frames = tuple(
        LandmarkFrame(observation.landmarks, observation.handedness, now)
        for now, observation in scripted[_PRESENCE_FRAMES:]
    )
    return Trajectory(frames=frames, handedness="Right")


def _add_gesture(
    store: Store,
    name: str,
    kind: str,
    action: dict[str, object],
) -> int:
    gesture = store.gestures.add(name)
    for exemplar_index in range(8):
        centered_index = exemplar_index - 3.5
        store.exemplars.add(
            gesture.id,
            _candidate_trajectory(
                kind,
                amplitude=1.0 + centered_index * 0.025,
                noise=0.004 + exemplar_index * 0.001,
                phase=exemplar_index * 0.41,
            ),
        )
    store.mappings.set(gesture.id, action)
    return gesture.id


def _seed_two_gestures(store: Store) -> dict[str, int]:
    return {
        "horizontal": _add_gesture(
            store,
            "Horizontal",
            "horizontal",
            {"kind": "switch_next"},
        ),
        "vertical": _add_gesture(
            store,
            "Vertical",
            "vertical",
            {"kind": "scroll", "amount": 2},
        ),
    }


def _make_pipeline(
    store: Store,
    *,
    gate: ConfidenceGate,
) -> tuple[Pipeline, FakeClock]:
    config = _configured_app()
    clock = FakeClock()
    pipeline = Pipeline(
        config,
        ActionController(config.input.pointer_pixels_per_palm, practice=True),
        store=store,
        metrics=Metrics(clock=clock),
        gate=gate,
        profile=_profile(),
        clock=clock,
    )
    return pipeline, clock


def _feed_pipeline(
    pipeline: Pipeline,
    clock: FakeClock,
    kind: str,
) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    for now, observation in _scripted_observations(kind):
        clock.now = now
        events.extend(pipeline.process(observation, now))
    return events


def test_matched_segment_dispatches_mapping_and_is_undoable() -> None:
    with Store(":memory:") as store:
        _seed_two_gestures(store)
        pipeline, clock = _make_pipeline(store, gate=_strict_gate())

        events = _feed_pipeline(pipeline, clock, "horizontal")

        candidates = [event for event in events if event["type"] == "candidate"]
        actions = [event for event in events if event["type"] == "action"]
        assert [(event["gate"], event["reason"]) for event in candidates] == [
            ("fire", "ok")
        ]
        assert [event["kind"] for event in actions] == ["switch_next"]
        assert [
            (event.kind, event.values) for event in pipeline.controller.sink.events
        ] == [("hotkey", (VK_ALT, VK_TAB))]

        clock.now += 0.1
        undo_events = pipeline.undo()

        assert [event["kind"] for event in undo_events] == ["switch_previous"]
        assert [
            (event.kind, event.values) for event in pipeline.controller.sink.events
        ] == [
            ("hotkey", (VK_ALT, VK_TAB)),
            ("hotkey", (VK_ALT, VK_SHIFT, VK_TAB)),
        ]


def test_noise_segment_abstains_without_dispatch() -> None:
    with Store(":memory:") as store:
        _seed_two_gestures(store)
        pipeline, clock = _make_pipeline(store, gate=_strict_gate())

        events = _feed_pipeline(pipeline, clock, "circle")

        candidates = [event for event in events if event["type"] == "candidate"]
        assert len(candidates) == 1
        assert candidates[0]["gate"] == "abstain"
        assert candidates[0]["reason"] == "t1"
        assert candidates[0]["confidence"] < 0.9
        assert not any(event["type"] == "action" for event in events)
        assert pipeline.controller.sink.events == []


def test_empty_library_preserves_legacy_candidate_event() -> None:
    with Store(":memory:") as store:
        pipeline, clock = _make_pipeline(
            store,
            gate=ConfidenceGate(GateThresholds()),
        )

        events = _feed_pipeline(pipeline, clock, "horizontal")

        candidates = [event for event in events if event["type"] == "candidate"]
        assert candidates == [
            candidate_event(
                gate="fire",
                reason="ok",
                confidence=1.0,
                ts=_SCRIPT_END,
            )
        ]
        assert not any(event["type"] == "action" for event in events)
        assert pipeline.controller.sink.events == []


def test_daemon_refresh_matcher_picks_up_new_gesture() -> None:
    with Store(":memory:") as store:
        save_profile(store, _profile())
        horizontal_id = _add_gesture(
            store,
            "Horizontal",
            "horizontal",
            {"kind": "switch_next"},
        )
        config = _configured_app()
        controller = ActionController(
            config.input.pointer_pixels_per_palm,
            practice=True,
        )
        daemon = Daemon(
            config,
            practice=True,
            controller=controller,
            store=store,
        )
        try:
            vertical_id = _add_gesture(
                store,
                "Vertical",
                "vertical",
                {"kind": "scroll", "amount": 2},
            )
            assert daemon.pipeline.matcher is not None
            assert (
                daemon.pipeline.matcher.match(_candidate_trajectory("vertical")).gesture_id
                == horizontal_id
            )
            raw_command = json.dumps(
                {"v": 1, "type": "command", "name": "refresh_matcher"}
            )
            assert parse_command(raw_command)["name"] == "refresh_matcher"

            refresh_events = daemon.command("refresh_matcher")

            assert [event["type"] for event in refresh_events] == ["status"]
            assert (
                daemon.pipeline.matcher.match(_candidate_trajectory("vertical")).gesture_id
                == vertical_id
            )
            daemon.pipeline.gate = _strict_gate()
            events: list[dict[str, object]] = []
            for now, observation in _scripted_observations("vertical"):
                events.extend(daemon.feed(observation, now))

            assert [
                event["kind"] for event in events if event["type"] == "action"
            ] == ["scroll"]
            assert [
                (event.kind, event.values) for event in controller.sink.events
            ] == [("scroll_vertical", (2,))]
        finally:
            daemon.stop()


def test_unknown_mapping_kind_is_logged_and_skipped(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with Store(":memory:") as store:
        _add_gesture(
            store,
            "Broken",
            "horizontal",
            {"kind": "not_an_action"},
        )
        pipeline, clock = _make_pipeline(store, gate=_strict_gate())

        with caplog.at_level(logging.WARNING, logger="aircontrol.pipeline"):
            events = _feed_pipeline(pipeline, clock, "horizontal")

        assert [
            event["gate"] for event in events if event["type"] == "candidate"
        ] == ["fire"]
        assert not any(event["type"] == "action" for event in events)
        assert pipeline.controller.sink.events == []
        assert "Ignoring malformed gesture mapping" in caplog.text
