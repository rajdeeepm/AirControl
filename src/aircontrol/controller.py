from __future__ import annotations

from aircontrol.domain import Action, ActionKind
from aircontrol.input_sink import DryRunInputSink, InputSink, VK_ESCAPE, WindowsInputSink


class ActionController:
    """Maps gesture-engine actions to a real or simulated desktop input sink."""

    def __init__(self, pointer_pixels_per_palm: float, practice: bool = False):
        self.pointer_pixels_per_palm = pointer_pixels_per_palm
        self.sink: InputSink = DryRunInputSink() if practice else WindowsInputSink()

    def dispatch(self, action: Action) -> str | None:
        if action.kind == ActionKind.MOVE_POINTER:
            dx = round(action.dx * self.pointer_pixels_per_palm)
            dy = round(action.dy * self.pointer_pixels_per_palm)
            if dx or dy:
                self.sink.move_relative(dx, dy)
            return None
        if action.kind == ActionKind.LEFT_DOWN:
            self.sink.left_down()
            return "PINCH · BUTTON DOWN"
        if action.kind == ActionKind.LEFT_UP:
            self.sink.left_up()
            return "PINCH RELEASED"
        if action.kind == ActionKind.SCROLL:
            self.sink.scroll_vertical(action.amount)
            return "SCROLL UP" if action.amount > 0 else "SCROLL DOWN"
        if action.kind == ActionKind.SWITCH_NEXT:
            self.sink.alt_tab()
            return "NEXT APP"
        if action.kind == ActionKind.SWITCH_PREVIOUS:
            self.sink.alt_shift_tab()
            return "PREVIOUS APP"
        if action.kind == ActionKind.TASK_VIEW:
            self.sink.win_tab()
            return "TASK VIEW"
        if action.kind == ActionKind.SHOW_DESKTOP:
            self.sink.win_d()
            return "SHOW DESKTOP"
        if action.kind == ActionKind.ESCAPE:
            self.sink.hotkey(VK_ESCAPE)
            return "UNDO · ESC"
        raise ValueError(f"Unsupported action: {action.kind}")

    def dispatch_all(self, actions: list[Action]) -> list[str]:
        descriptions = []
        for action in actions:
            description = self.dispatch(action)
            if description:
                descriptions.append(description)
        return descriptions

    def release_all(self) -> None:
        self.sink.release_all()

    def close(self) -> None:
        close = getattr(self.sink, "close", None)
        if close is not None:
            close()
