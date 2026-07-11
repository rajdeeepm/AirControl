from __future__ import annotations

import pytest

from aircontrol.config import AppConfig
from aircontrol.controller import ActionController
from aircontrol.domain import HandObservation, Point3D
from aircontrol.gate import ConfidenceGate, GateThresholds
from aircontrol.metrics import Metrics
from aircontrol.pipeline import Pipeline
from aircontrol.store import Store


FINGER_LAYOUT = {
    "index": (5, 6, 7, 8, 0.43, 0.62),
    "middle": (9, 10, 11, 12, 0.49, 0.60),
    "ring": (13, 14, 15, 16, 0.55, 0.62),
    "pinky": (17, 18, 19, 20, 0.61, 0.66),
}


def make_hand(
    extended: tuple[str, ...] = (),
    *,
    dx: float = 0.0,
) -> HandObservation:
    points = [Point3D(0.5, 0.8) for _ in range(21)]
    points[0] = Point3D(0.5, 0.82)
    points[1] = Point3D(0.40, 0.72)
    points[2] = Point3D(0.34, 0.66)
    points[3] = Point3D(0.30, 0.60)
    points[4] = Point3D(0.27, 0.55)
    for name, (mcp, pip, dip, tip, x, y) in FINGER_LAYOUT.items():
        points[mcp] = Point3D(x, y)
        if name in extended:
            points[pip] = Point3D(x, y - 0.13)
            points[dip] = Point3D(x, y - 0.24)
            points[tip] = Point3D(x, y - 0.34)
        else:
            points[pip] = Point3D(x, y - 0.07)
            points[dip] = Point3D(x + 0.035, y - 0.01)
            points[tip] = Point3D(x + 0.018, y + 0.045)
    landmarks = tuple(Point3D(point.x + dx, point.y, point.z) for point in points)
    return HandObservation(
        landmarks=landmarks,
        handedness="Left",
        confidence=0.99,
        image_width=1,
        image_height=1,
    )


@pytest.fixture
def in_memory_store():
    with Store(":memory:") as store:
        yield store


def make_pipeline(
    store: Store,
    *,
    gate: ConfidenceGate | None = None,
    buffer_capacity: int = 75,
) -> Pipeline:
    config = AppConfig.defaults()
    config.pipeline.buffer_capacity = buffer_capacity
    controller = ActionController(760.0, practice=True)
    return Pipeline(
        config,
        controller,
        store=store,
        metrics=Metrics(),
        gate=gate or ConfidenceGate(GateThresholds()),
    )


def drive_pointer_action(pipeline: Pipeline) -> list[dict]:
    config = pipeline.engine.config
    events: list[dict] = []
    now = 0.0
    open_palm = make_hand(("index", "middle", "ring", "pinky"))
    while now <= config.arm_hold_seconds + 0.1:
        events.extend(pipeline.process(open_palm, now))
        now += 0.05

    pointer = make_hand(("index",))
    events.extend(pipeline.process(pointer, now))
    now += config.stability_seconds + 0.01
    events.extend(pipeline.process(pointer, now))
    now += 0.05
    events.extend(pipeline.process(make_hand(("index",), dx=0.08), now))
    return events


def test_open_palm_then_pointer_motion_dispatches_move_pointer(in_memory_store) -> None:
    pipeline = make_pipeline(in_memory_store)

    events = drive_pointer_action(pipeline)

    assert pipeline.engine.armed
    assert any(event.kind == "move_relative" for event in pipeline.controller.sink.events)
    assert any(
        event["type"] == "action" and event["kind"] == "move_pointer"
        for event in events
    )


def test_blocking_gate_suppresses_dispatch(in_memory_store) -> None:
    gate = ConfidenceGate(GateThresholds(t1_top1=2.0))
    pipeline = make_pipeline(in_memory_store, gate=gate)

    events = drive_pointer_action(pipeline)

    assert pipeline.controller.sink.events == []
    assert any(
        event["type"] == "candidate"
        and event["gate"] == "abstain"
        and event["reason"] == "t1"
        for event in events
    )
    assert not any(event["type"] == "action" for event in events)


def test_buffer_grows_with_observations_and_is_bounded(in_memory_store) -> None:
    pipeline = make_pipeline(in_memory_store, buffer_capacity=3)

    for index in range(5):
        pipeline.process(make_hand(("index",), dx=index * 0.01), float(index))

    assert len(pipeline.buffer) == 3
    assert [frame.timestamp for frame in pipeline.buffer.last(3).frames] == [2.0, 3.0, 4.0]


def test_emitted_events_match_ipc_v1_schema(in_memory_store) -> None:
    pipeline = make_pipeline(in_memory_store)

    events = drive_pointer_action(pipeline)

    required = {
        "status": {
            "v",
            "type",
            "armed",
            "raw_pose",
            "active_pose",
            "hold_progress",
            "hand_visible",
            "status_text",
        },
        "action": {"v", "type", "kind", "confidence", "description", "ts"},
        "candidate": {"v", "type", "gate", "reason", "confidence", "ts"},
    }
    assert {event["type"] for event in events} == set(required)
    for event in events:
        assert event["v"] == 1
        assert set(event) == required[event["type"]]
