from __future__ import annotations

import pytest

from aircontrol.controller import ActionController
from aircontrol.domain import Action, ActionKind
from aircontrol.input_sink import VK_ESCAPE
from aircontrol.metrics import Metrics
from aircontrol.undo import REVERSIBLE, UndoManager


class FakeClock:
    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.mark.parametrize(
    ("action", "expected"),
    [
        (Action(ActionKind.SWITCH_NEXT), Action(ActionKind.SWITCH_PREVIOUS)),
        (Action(ActionKind.SWITCH_PREVIOUS), Action(ActionKind.SWITCH_NEXT)),
        (Action(ActionKind.SCROLL, amount=4), Action(ActionKind.SCROLL, amount=-4)),
        (Action(ActionKind.SHOW_DESKTOP), Action(ActionKind.SHOW_DESKTOP)),
        (Action(ActionKind.TASK_VIEW), Action(ActionKind.ESCAPE)),
    ],
)
def test_reversible_mapping_returns_the_expected_inverse(
    action: Action,
    expected: Action,
) -> None:
    assert REVERSIBLE[action.kind](action) == expected


def test_reversible_mapping_has_exactly_the_supported_action_kinds() -> None:
    assert set(REVERSIBLE) == {
        ActionKind.SWITCH_NEXT,
        ActionKind.SWITCH_PREVIOUS,
        ActionKind.SCROLL,
        ActionKind.SHOW_DESKTOP,
        ActionKind.TASK_VIEW,
    }


def test_undo_expires_after_the_configured_window() -> None:
    clock = FakeClock()
    metrics = Metrics(clock=clock)
    undo = UndoManager(metrics, window_seconds=3.0, clock=clock)
    undo.note_fire(Action(ActionKind.SWITCH_NEXT))

    clock.advance(3.1)

    assert undo.try_undo() is None
    assert metrics.snapshot().fp_per_hour == 0.0


def test_successful_undo_logs_one_false_positive_and_clears_pending_action() -> None:
    clock = FakeClock()
    metrics = Metrics(clock=clock)
    undo = UndoManager(metrics, clock=clock)
    undo.note_fire(Action(ActionKind.SWITCH_NEXT))
    clock.advance(1.0)

    assert undo.try_undo() == Action(ActionKind.SWITCH_PREVIOUS)
    assert undo.try_undo() is None
    assert metrics.snapshot().fp_per_hour == pytest.approx(3_600.0)


def test_irreversible_action_clears_a_pending_undo() -> None:
    clock = FakeClock()
    metrics = Metrics(clock=clock)
    undo = UndoManager(metrics, clock=clock)
    undo.note_fire(Action(ActionKind.SWITCH_NEXT))

    undo.note_fire(Action(ActionKind.MOVE_POINTER, dx=0.25, dy=-0.1))

    assert undo.try_undo() is None
    assert metrics.snapshot().fp_per_hour == 0.0


def test_controller_dispatches_escape_as_an_undo_hotkey_in_practice_mode() -> None:
    controller = ActionController(pointer_pixels_per_palm=800, practice=True)

    description = controller.dispatch(Action(ActionKind.ESCAPE))

    event = controller.sink.events[-1]
    assert description == "UNDO · ESC"
    assert "UNDO" in description
    assert event.kind == "hotkey"
    assert event.values == (VK_ESCAPE,)
    assert event.description == "Hotkey: Esc"
