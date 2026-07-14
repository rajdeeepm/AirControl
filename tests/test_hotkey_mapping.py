from __future__ import annotations

import logging

import pytest

from aircontrol.controller import ActionController
from aircontrol.domain import Action, ActionKind
from aircontrol.store import Store
from aircontrol.undo import REVERSIBLE
from test_matcher_integration import (
    _add_gesture,
    _feed_pipeline,
    _make_pipeline,
    _strict_gate,
)


VK_CONTROL = 0x11
VK_ALT = 0x12
VK_TAB = 0x09
VK_DELETE = 0x2E
VK_L = 0x4C
VK_T = 0x54
VK_LWIN = 0x5B
VK_F4 = 0x73


def _pipeline_for_mapping(
    store: Store,
    action: dict[str, object],
    *,
    allow_risky_hotkeys: bool = False,
):
    _add_gesture(store, "Hotkey", "horizontal", action)
    pipeline, clock = _make_pipeline(store, gate=_strict_gate())
    pipeline.config.input.allow_risky_hotkeys = allow_risky_hotkeys
    return pipeline, clock


def test_hotkey_mapping_dispatches_through_dry_run_sink() -> None:
    with Store(":memory:") as store:
        pipeline, clock = _pipeline_for_mapping(
            store,
            {"kind": "hotkey", "keys": [VK_CONTROL, VK_T]},
        )

        events = _feed_pipeline(pipeline, clock, "horizontal")

        actions = [event for event in events if event["type"] == "action"]
        assert [(event["kind"], event["description"]) for event in actions] == [
            ("hotkey", "HOTKEY · VK_0x11 + VK_0x54")
        ]
        assert [
            (event.kind, event.values) for event in pipeline.controller.sink.events
        ] == [("hotkey", (VK_CONTROL, VK_T))]


def test_controller_rejects_a_hotkey_without_keys() -> None:
    controller = ActionController(pointer_pixels_per_palm=800, practice=True)

    with pytest.raises(ValueError, match="at least one"):
        controller.dispatch(Action(ActionKind.HOTKEY))

    assert controller.sink.events == []


def test_controller_describes_known_key_names_and_vk_fallbacks() -> None:
    controller = ActionController(pointer_pixels_per_palm=800, practice=True)

    description = controller.dispatch(
        Action(ActionKind.HOTKEY, keys=(VK_ALT, VK_TAB, VK_T))
    )

    assert description == "HOTKEY · Alt + Tab + VK_0x54"
    assert controller.sink.events[-1].values == (VK_ALT, VK_TAB, VK_T)


@pytest.mark.parametrize(
    "keys",
    [
        pytest.param([], id="empty"),
        pytest.param([VK_CONTROL, "T"], id="non-integer"),
        pytest.param([True], id="boolean"),
        pytest.param([1, 2, 3, 4, 5], id="more-than-four"),
        pytest.param([0], id="below-range"),
        pytest.param([0xFF], id="above-range"),
        pytest.param("Ctrl+T", id="not-a-list"),
    ],
)
def test_malformed_hotkey_keys_are_logged_and_skipped(
    keys: object,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with Store(":memory:") as store:
        pipeline, clock = _pipeline_for_mapping(
            store,
            {"kind": "hotkey", "keys": keys},
        )

        with caplog.at_level(logging.WARNING, logger="aircontrol.pipeline"):
            events = _feed_pipeline(pipeline, clock, "horizontal")

        assert not any(event["type"] == "action" for event in events)
        assert pipeline.controller.sink.events == []
        assert "Ignoring malformed gesture mapping" in caplog.text


@pytest.mark.parametrize(
    "keys",
    [
        pytest.param([VK_F4, VK_ALT], id="alt-f4-order-insensitive"),
        pytest.param([VK_L, VK_LWIN], id="win-l-order-insensitive"),
        pytest.param(
            [VK_DELETE, 0x10, VK_ALT, VK_CONTROL],
            id="ctrl-alt-delete-contained",
        ),
    ],
)
def test_risky_hotkeys_are_blocked_by_default(
    keys: list[int],
    caplog: pytest.LogCaptureFixture,
) -> None:
    with Store(":memory:") as store:
        pipeline, clock = _pipeline_for_mapping(
            store,
            {"kind": "hotkey", "keys": keys},
        )

        with caplog.at_level(logging.WARNING, logger="aircontrol.pipeline"):
            events = _feed_pipeline(pipeline, clock, "horizontal")

        assert not any(event["type"] == "action" for event in events)
        assert pipeline.controller.sink.events == []
        assert "risky hotkey" in caplog.text.lower()


def test_risky_hotkey_is_allowed_when_configured() -> None:
    with Store(":memory:") as store:
        pipeline, clock = _pipeline_for_mapping(
            store,
            {"kind": "hotkey", "keys": [VK_ALT, VK_F4]},
            allow_risky_hotkeys=True,
        )

        events = _feed_pipeline(pipeline, clock, "horizontal")

        assert [
            event["kind"] for event in events if event["type"] == "action"
        ] == ["hotkey"]
        assert [
            (event.kind, event.values) for event in pipeline.controller.sink.events
        ] == [("hotkey", (VK_ALT, VK_F4))]


def test_hotkey_fire_clears_pending_undo_and_dispatches_no_undo_action() -> None:
    with Store(":memory:") as store:
        pipeline, clock = _pipeline_for_mapping(
            store,
            {"kind": "hotkey", "keys": [VK_CONTROL, VK_T]},
        )
        pipeline._undo.note_fire(Action(ActionKind.SWITCH_NEXT))

        _feed_pipeline(pipeline, clock, "horizontal")
        events_before_undo = list(pipeline.controller.sink.events)

        assert ActionKind.HOTKEY not in REVERSIBLE
        assert pipeline.undo() == []
        assert pipeline.controller.sink.events == events_before_undo
