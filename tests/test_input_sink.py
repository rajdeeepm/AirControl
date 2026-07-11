from __future__ import annotations

import ctypes

import pytest

from aircontrol.input_sink import (
    DryRunInputSink,
    InputSink,
    VK_ALT,
    VK_D,
    VK_LWIN,
    VK_SHIFT,
    VK_TAB,
    WHEEL_DELTA,
    WindowsInputSink,
)
import aircontrol.input_sink as input_sink_module


class RecordingBackend:
    def __init__(self) -> None:
        self.batches: list[list[tuple[int, int, int, int, int]]] = []

    def send(self, inputs: object) -> None:
        batch = []
        for item in inputs:
            if item.type == input_sink_module._INPUT_MOUSE:
                batch.append((item.type, item.mi.dx, item.mi.dy, item.mi.mouseData, item.mi.dwFlags))
            else:
                batch.append((item.type, item.ki.wVk, item.ki.dwFlags, 0, 0))
        self.batches.append(batch)


def test_dry_run_is_fake_friendly_and_descriptive() -> None:
    sink = DryRunInputSink()

    sink.move_relative(8, -3)
    sink.left_down()
    sink.scroll_vertical(-2)
    sink.alt_tab()
    sink.release_all()

    assert isinstance(sink, InputSink)
    assert [event.kind for event in sink.events] == [
        "move_relative",
        "left_down",
        "scroll_vertical",
        "hotkey",
        "left_up",
    ]
    assert sink.events[2].values == (-2,)
    assert sink.events[3].values == (VK_ALT, VK_TAB)
    assert sink.last_description == "Left button up"
    assert not sink.left_is_down


def test_relative_motion_and_signed_wheel_delta_use_mouse_sendinput() -> None:
    backend = RecordingBackend()
    sink = WindowsInputSink(backend=backend)

    sink.move_relative(17, -9)
    sink.scroll_vertical(-2)

    assert backend.batches[0] == [
        (input_sink_module._INPUT_MOUSE, 17, -9, 0, input_sink_module._MOUSEEVENTF_MOVE)
    ]
    assert backend.batches[1] == [
        (
            input_sink_module._INPUT_MOUSE,
            0,
            0,
            ctypes.c_uint32(-2 * WHEEL_DELTA).value,
            input_sink_module._MOUSEEVENTF_WHEEL,
        )
    ]


def test_left_button_is_idempotent_and_release_all_recovers() -> None:
    backend = RecordingBackend()
    sink = WindowsInputSink(backend=backend)

    sink.left_down()
    sink.left_down()
    assert sink.left_is_down

    sink.release_all()

    assert not sink.left_is_down
    assert [batch[0][4] for batch in backend.batches] == [
        input_sink_module._MOUSEEVENTF_LEFTDOWN,
        input_sink_module._MOUSEEVENTF_LEFTUP,
    ]


def test_release_all_on_a_fresh_sink_injects_nothing() -> None:
    backend = RecordingBackend()
    sink = WindowsInputSink(backend=backend)

    sink.release_all()

    assert backend.batches == []
    assert sink.events == []


def test_failed_left_up_remains_owned_and_is_retried() -> None:
    class FailFirstLeftUpBackend(RecordingBackend):
        def __init__(self) -> None:
            super().__init__()
            self.failed = False

        def send(self, inputs: object) -> None:
            items = list(inputs)
            if (
                not self.failed
                and items
                and items[0].type == input_sink_module._INPUT_MOUSE
                and items[0].mi.dwFlags == input_sink_module._MOUSEEVENTF_LEFTUP
            ):
                self.failed = True
                raise OSError("simulated release failure")
            super().send(items)

    backend = FailFirstLeftUpBackend()
    sink = WindowsInputSink(backend=backend)
    sink.left_down()

    with pytest.raises(OSError, match="release failure"):
        sink.left_up()
    assert sink.left_is_down

    sink.release_all()
    assert not sink.left_is_down
    assert backend.batches[-1][0][4] == input_sink_module._MOUSEEVENTF_LEFTUP


def test_context_manager_releases_a_held_button_on_exception() -> None:
    backend = RecordingBackend()

    with pytest.raises(RuntimeError, match="gesture failed"):
        with WindowsInputSink(backend=backend) as sink:
            sink.left_down()
            raise RuntimeError("gesture failed")

    assert [batch[0][4] for batch in backend.batches] == [
        input_sink_module._MOUSEEVENTF_LEFTDOWN,
        input_sink_module._MOUSEEVENTF_LEFTUP,
    ]
    assert not sink.left_is_down


@pytest.mark.parametrize(
    ("method_name", "keys"),
    [
        ("alt_tab", (VK_ALT, VK_TAB)),
        ("alt_shift_tab", (VK_ALT, VK_SHIFT, VK_TAB)),
        ("win_tab", (VK_LWIN, VK_TAB)),
        ("win_d", (VK_LWIN, VK_D)),
    ],
)
def test_complete_hotkey_sequences_press_in_order_and_release_in_reverse(
    method_name: str, keys: tuple[int, ...]
) -> None:
    backend = RecordingBackend()
    sink = WindowsInputSink(backend=backend)

    getattr(sink, method_name)()

    expected = [
        (input_sink_module._INPUT_KEYBOARD, key, 0, 0, 0) for key in keys
    ] + [
        (
            input_sink_module._INPUT_KEYBOARD,
            key,
            input_sink_module._KEYEVENTF_KEYUP,
            0,
            0,
        )
        for key in reversed(keys)
    ]
    assert backend.batches == [expected]


def test_partial_hotkey_failure_attempts_emergency_modifier_release() -> None:
    class FailFirstBatchBackend(RecordingBackend):
        def __init__(self) -> None:
            super().__init__()
            self.calls = 0

        def send(self, inputs: object) -> None:
            self.calls += 1
            if self.calls == 1:
                raise OSError("partial SendInput")
            super().send(inputs)

    backend = FailFirstBatchBackend()
    sink = WindowsInputSink(backend=backend)

    with pytest.raises(OSError, match="partial SendInput"):
        sink.hotkey(VK_ALT, VK_SHIFT, VK_TAB)

    assert backend.batches == [[
        (input_sink_module._INPUT_KEYBOARD, VK_TAB, input_sink_module._KEYEVENTF_KEYUP, 0, 0),
        (input_sink_module._INPUT_KEYBOARD, VK_SHIFT, input_sink_module._KEYEVENTF_KEYUP, 0, 0),
        (input_sink_module._INPUT_KEYBOARD, VK_ALT, input_sink_module._KEYEVENTF_KEYUP, 0, 0),
    ]]
    sink.release_all()
    assert len(backend.batches) == 1


def test_enabling_dry_run_first_releases_a_physically_held_button() -> None:
    backend = RecordingBackend()
    sink = WindowsInputSink(backend=backend)
    sink.left_down()

    sink.set_dry_run(True)

    assert sink.dry_run
    assert not sink.left_is_down
    assert [batch[0][4] for batch in backend.batches] == [
        input_sink_module._MOUSEEVENTF_LEFTDOWN,
        input_sink_module._MOUSEEVENTF_LEFTUP,
    ]


def test_import_and_dry_run_do_not_touch_windows_dll(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_if_loaded() -> None:
        raise AssertionError("user32 backend must stay lazy")

    monkeypatch.setattr(input_sink_module, "_User32SendInputBackend", fail_if_loaded)
    sink = WindowsInputSink(dry_run=True)

    sink.move_relative(1, 1)
    sink.hotkey(VK_LWIN, VK_D)

    assert len(sink.events) == 2


def test_invalid_values_are_rejected_before_injection() -> None:
    backend = RecordingBackend()
    sink = WindowsInputSink(backend=backend)

    with pytest.raises(TypeError):
        sink.move_relative(1.5, 2)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        sink.hotkey()
    with pytest.raises(ValueError):
        sink.hotkey(0x1_0000)

    assert backend.batches == []
