from __future__ import annotations

from unittest.mock import Mock

from aircontrol import desktop


class _FocusFlag:
    def __init__(self) -> None:
        self.requested = True

    def take_focus_request(self) -> bool:
        requested = self.requested
        self.requested = False
        return requested


def test_consume_focus_request_raises_and_restores_main_window() -> None:
    daemon = _FocusFlag()
    window = Mock()

    assert desktop._consume_focus_request(daemon, window) is True
    window.show.assert_called_once_with()
    window.restore.assert_called_once_with()
    assert daemon.requested is False

    assert desktop._consume_focus_request(daemon, window) is False
    window.show.assert_called_once_with()
    window.restore.assert_called_once_with()


def test_consume_focus_request_is_no_op_safe() -> None:
    daemon = Mock()
    daemon.take_focus_request.side_effect = RuntimeError("daemon stopped")

    assert desktop._consume_focus_request(daemon, object()) is False
