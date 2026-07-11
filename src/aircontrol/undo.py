from __future__ import annotations

import time
from collections.abc import Callable

from aircontrol.domain import Action, ActionKind
from aircontrol.metrics import Metrics


REVERSIBLE: dict[ActionKind, Callable[[Action], Action]] = {
    ActionKind.SWITCH_NEXT: lambda action: Action(ActionKind.SWITCH_PREVIOUS),
    ActionKind.SWITCH_PREVIOUS: lambda action: Action(ActionKind.SWITCH_NEXT),
    ActionKind.SCROLL: lambda action: Action(ActionKind.SCROLL, amount=-action.amount),
    ActionKind.SHOW_DESKTOP: lambda action: Action(ActionKind.SHOW_DESKTOP),
    ActionKind.TASK_VIEW: lambda action: Action(ActionKind.ESCAPE),
}


class UndoManager:
    def __init__(
        self,
        metrics: Metrics,
        *,
        window_seconds: float = 3.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._metrics = metrics
        self._window_seconds = window_seconds
        self._clock = clock
        self._pending: tuple[Action, float] | None = None

    def note_fire(self, action: Action) -> None:
        if action.kind not in REVERSIBLE:
            self._pending = None
            return
        self._pending = (action, self._clock())

    def try_undo(self) -> Action | None:
        if self._pending is None:
            return None

        action, fired_at = self._pending
        self._pending = None
        if self._clock() - fired_at > self._window_seconds:
            return None

        inverse = REVERSIBLE[action.kind](action)
        self._metrics.note_false_positive()
        return inverse


__all__ = ["REVERSIBLE", "UndoManager"]
