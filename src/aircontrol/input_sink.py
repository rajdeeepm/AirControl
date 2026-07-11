"""Windows input injection and a safe dry-run implementation.

The module is importable on every platform.  The Win32 DLL is loaded lazily, only
when a :class:`WindowsInputSink` actually needs to inject an event.  Tests and
non-Windows callers can therefore use ``DryRunInputSink`` or inject a fake
``SendInputBackend`` without touching ``user32.dll``.
"""

from __future__ import annotations

import ctypes
import os
from dataclasses import dataclass
from typing import Protocol, Sequence, runtime_checkable


# Win32 virtual-key codes used by the application.
VK_TAB = 0x09
VK_SHIFT = 0x10
VK_ALT = 0x12
VK_LWIN = 0x5B
VK_D = 0x44

WHEEL_DELTA = 120

# INPUT / mouse / keyboard flags from winuser.h.
_INPUT_MOUSE = 0
_INPUT_KEYBOARD = 1
_MOUSEEVENTF_MOVE = 0x0001
_MOUSEEVENTF_LEFTDOWN = 0x0002
_MOUSEEVENTF_LEFTUP = 0x0004
_MOUSEEVENTF_WHEEL = 0x0800
_KEYEVENTF_KEYUP = 0x0002

_INT32_MIN = -(2**31)
_INT32_MAX = 2**31 - 1


# Fixed-width aliases are intentional: Python's c_long is 64-bit on some
# non-Windows hosts, while Win32 LONG and DWORD are always 32-bit.
_LONG = ctypes.c_int32
_DWORD = ctypes.c_uint32
_WORD = ctypes.c_uint16
_ULONG_PTR = ctypes.c_uint64 if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_uint32


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = (
        ("dx", _LONG),
        ("dy", _LONG),
        ("mouseData", _DWORD),
        ("dwFlags", _DWORD),
        ("time", _DWORD),
        ("dwExtraInfo", _ULONG_PTR),
    )


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = (
        ("wVk", _WORD),
        ("wScan", _WORD),
        ("dwFlags", _DWORD),
        ("time", _DWORD),
        ("dwExtraInfo", _ULONG_PTR),
    )


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = (("uMsg", _DWORD), ("wParamL", _WORD), ("wParamH", _WORD))


class _INPUTUNION(ctypes.Union):
    _fields_ = (("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT), ("hi", _HARDWAREINPUT))


class _INPUT(ctypes.Structure):
    _anonymous_ = ("data",)
    _fields_ = (("type", _DWORD), ("data", _INPUTUNION))


@dataclass(frozen=True, slots=True)
class InputEvent:
    """A high-level input request retained for diagnostics and dry runs."""

    kind: str
    values: tuple[int, ...]
    description: str


@runtime_checkable
class InputSink(Protocol):
    """Fake-friendly boundary used by the gesture/action layer."""

    dry_run: bool
    events: list[InputEvent]

    def move_relative(self, dx_pixels: int, dy_pixels: int) -> None: ...

    def left_down(self) -> None: ...

    def left_up(self) -> None: ...

    def scroll_vertical(self, notches: int) -> None: ...

    def hotkey(self, *virtual_key_codes: int) -> None: ...

    def release_all(self) -> None: ...


class SendInputBackend(Protocol):
    """Minimal injection seam; fakes only need to implement ``send``."""

    def send(self, inputs: Sequence[_INPUT]) -> None: ...


_KEY_NAMES = {
    VK_TAB: "Tab",
    VK_SHIFT: "Shift",
    VK_ALT: "Alt",
    VK_LWIN: "Win",
    VK_D: "D",
}


def _require_int(value: int, name: str, *, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _mouse_input(*, dx: int = 0, dy: int = 0, data: int = 0, flags: int) -> _INPUT:
    return _INPUT(
        type=_INPUT_MOUSE,
        mi=_MOUSEINPUT(
            dx=dx,
            dy=dy,
            mouseData=ctypes.c_uint32(data).value,
            dwFlags=flags,
            time=0,
            dwExtraInfo=0,
        ),
    )


def _keyboard_input(virtual_key_code: int, *, key_up: bool) -> _INPUT:
    return _INPUT(
        type=_INPUT_KEYBOARD,
        ki=_KEYBDINPUT(
            wVk=virtual_key_code,
            wScan=0,
            dwFlags=_KEYEVENTF_KEYUP if key_up else 0,
            time=0,
            dwExtraInfo=0,
        ),
    )


class _User32SendInputBackend:
    """Thin wrapper around user32.SendInput, instantiated lazily."""

    def __init__(self) -> None:
        if os.name != "nt":
            raise OSError("Windows input injection is only available on Windows")

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        send_input = user32.SendInput
        send_input.argtypes = (_DWORD, ctypes.POINTER(_INPUT), ctypes.c_int)
        send_input.restype = _DWORD
        self._send_input = send_input

    def send(self, inputs: Sequence[_INPUT]) -> None:
        if not inputs:
            return
        input_array = (_INPUT * len(inputs))(*inputs)
        sent = int(self._send_input(len(input_array), input_array, ctypes.sizeof(_INPUT)))
        if sent != len(input_array):
            error_code = ctypes.get_last_error()
            if error_code:
                raise OSError(error_code, ctypes.FormatError(error_code))
            raise OSError(f"SendInput accepted {sent} of {len(input_array)} events")


class WindowsInputSink:
    """Inject relative mouse and keyboard input through Win32 ``SendInput``.

    ``backend`` is intentionally injectable for unit tests.  Setting ``dry_run``
    records requests without loading user32 or modifying the desktop.
    """

    def __init__(
        self,
        *,
        dry_run: bool = False,
        backend: SendInputBackend | None = None,
    ) -> None:
        self.dry_run = bool(dry_run)
        self.events: list[InputEvent] = []
        self._backend = backend
        self._left_is_down = False
        self._physical_left_is_down = False
        self._uncertain_keys: list[int] = []
        self._closed = False

    @property
    def left_is_down(self) -> bool:
        return self._left_is_down

    @property
    def last_description(self) -> str | None:
        return self.events[-1].description if self.events else None

    @property
    def descriptions(self) -> tuple[str, ...]:
        return tuple(event.description for event in self.events)

    def set_dry_run(self, enabled: bool) -> None:
        """Change modes without ever stranding a physically held mouse button."""

        enabled = bool(enabled)
        if enabled and not self.dry_run and self._physical_left_is_down:
            self.release_all()
        self.dry_run = enabled

    def _record(self, kind: str, values: tuple[int, ...], description: str) -> None:
        self.events.append(InputEvent(kind, values, description))

    def _send(self, inputs: Sequence[_INPUT]) -> None:
        if self.dry_run:
            return
        if self._backend is None:
            self._backend = _User32SendInputBackend()
        self._backend.send(inputs)

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("input sink is closed")

    def move_relative(self, dx_pixels: int, dy_pixels: int) -> None:
        self._ensure_open()
        dx = _require_int(dx_pixels, "dx_pixels", minimum=_INT32_MIN, maximum=_INT32_MAX)
        dy = _require_int(dy_pixels, "dy_pixels", minimum=_INT32_MIN, maximum=_INT32_MAX)
        self._record("move_relative", (dx, dy), f"Move pointer by ({dx:+d}, {dy:+d}) px")
        if dx or dy:
            self._send((_mouse_input(dx=dx, dy=dy, flags=_MOUSEEVENTF_MOVE),))

    def left_down(self) -> None:
        self._ensure_open()
        if self._left_is_down:
            return
        self._record("left_down", (), "Left button down")
        self._left_is_down = True
        self._physical_left_is_down = not self.dry_run
        self._send((_mouse_input(flags=_MOUSEEVENTF_LEFTDOWN),))

    def left_up(self) -> None:
        self._ensure_open()
        if not self._left_is_down and not self._physical_left_is_down:
            return
        self._record("left_up", (), "Left button up")
        self._send((_mouse_input(flags=_MOUSEEVENTF_LEFTUP),))
        self._left_is_down = False
        self._physical_left_is_down = False

    def scroll_vertical(self, notches: int) -> None:
        self._ensure_open()
        count = _require_int(notches, "notches", minimum=_INT32_MIN, maximum=_INT32_MAX)
        delta = count * WHEEL_DELTA
        if not _INT32_MIN <= delta <= _INT32_MAX:
            raise ValueError("notches produces a wheel delta outside the signed 32-bit range")
        label = "notch" if abs(count) == 1 else "notches"
        self._record("scroll_vertical", (count,), f"Scroll vertically {count:+d} {label}")
        if count:
            self._send((_mouse_input(data=delta, flags=_MOUSEEVENTF_WHEEL),))

    def hotkey(self, *virtual_key_codes: int) -> None:
        self._ensure_open()
        if not virtual_key_codes:
            raise ValueError("hotkey requires at least one virtual-key code")
        keys = tuple(
            _require_int(code, "virtual_key_code", minimum=0, maximum=0xFFFF)
            for code in virtual_key_codes
        )
        names = " + ".join(_KEY_NAMES.get(key, f"VK_0x{key:02X}") for key in keys)
        self._record("hotkey", keys, f"Hotkey: {names}")
        sequence = tuple(_keyboard_input(key, key_up=False) for key in keys) + tuple(
            _keyboard_input(key, key_up=True) for key in reversed(keys)
        )
        for key in keys:
            if key not in self._uncertain_keys:
                self._uncertain_keys.append(key)
        try:
            self._send(sequence)
        except Exception:
            try:
                self._release_uncertain_keys()
            except Exception:
                pass
            raise
        else:
            self._uncertain_keys.clear()

    def alt_tab(self) -> None:
        self.hotkey(VK_ALT, VK_TAB)

    def alt_shift_tab(self) -> None:
        self.hotkey(VK_ALT, VK_SHIFT, VK_TAB)

    def win_tab(self) -> None:
        self.hotkey(VK_LWIN, VK_TAB)

    def win_d(self) -> None:
        self.hotkey(VK_LWIN, VK_D)

    def release_all(self) -> None:
        """Release only input that AirControl may currently own."""

        if self._closed:
            return
        errors: list[Exception] = []
        if self._left_is_down or self._physical_left_is_down:
            try:
                self.left_up()
            except Exception as exc:
                errors.append(exc)
        if self._uncertain_keys:
            try:
                self._release_uncertain_keys()
            except Exception as exc:
                errors.append(exc)
        if errors:
            raise errors[0]

    def _release_uncertain_keys(self) -> None:
        if not self._uncertain_keys:
            return
        sequence = tuple(
            _keyboard_input(key, key_up=True) for key in reversed(self._uncertain_keys)
        )
        self._send(sequence)
        self._uncertain_keys.clear()

    def close(self) -> None:
        if self._closed:
            return
        self.release_all()
        self._closed = True

    def __enter__(self) -> "WindowsInputSink":
        self._ensure_open()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()


class DryRunInputSink(WindowsInputSink):
    """A no-side-effect sink suitable for demos, tests, and gesture tuning."""

    def __init__(self) -> None:
        super().__init__(dry_run=True)


# A short alias is convenient for callers that select a sink by platform.
Win32InputSink = WindowsInputSink


__all__ = [
    "DryRunInputSink",
    "InputEvent",
    "InputSink",
    "SendInputBackend",
    "VK_ALT",
    "VK_D",
    "VK_LWIN",
    "VK_SHIFT",
    "VK_TAB",
    "WHEEL_DELTA",
    "Win32InputSink",
    "WindowsInputSink",
]
