from __future__ import annotations

import pytest

from aircontrol.config import AppConfig
from aircontrol.controller import ActionController
from aircontrol.domain import Action, ActionKind
from aircontrol.gate import ConfidenceGate, GateThresholds
from aircontrol.metrics import Metrics
from aircontrol.pipeline import Pipeline
from aircontrol.store import Store


@pytest.fixture
def in_memory_store():
    with Store(":memory:") as store:
        yield store


def make_pipeline(store: Store) -> Pipeline:
    config = AppConfig.defaults()
    controller = ActionController(760.0, practice=True)
    return Pipeline(
        config,
        controller,
        store=store,
        metrics=Metrics(),
        gate=ConfidenceGate(GateThresholds(t1_top1=2.0)),
    )


def test_left_up_bypasses_blocking_gate(in_memory_store) -> None:
    pipeline = make_pipeline(in_memory_store)

    blocked_events = pipeline._gated_action_events(
        [Action(ActionKind.LEFT_DOWN)],
        now=1.0,
    )

    assert not any(event.kind == "left_down" for event in pipeline.controller.sink.events)
    assert not any(event["type"] == "action" for event in blocked_events)

    # Prime the dry-run sink so a dispatched release is observable as an input event.
    pipeline.controller.sink.left_down()
    pipeline.controller.sink.events.clear()

    release_events = pipeline._gated_action_events(
        [Action(ActionKind.LEFT_UP)],
        now=2.0,
    )

    assert any(event.kind == "left_up" for event in pipeline.controller.sink.events)
    assert release_events == [
        {
            "v": 1,
            "type": "action",
            "kind": "left_up",
            "confidence": 1.0,
            "description": "PINCH RELEASED",
            "ts": 2.0,
        }
    ]
