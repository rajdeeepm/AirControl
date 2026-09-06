"""macOS input injection through Quartz.

:class:`MacInputSink` is the macOS counterpart to
:class:`~aircontrol.input_sink.WindowsInputSink`.  Both inherit their
bookkeeping from :class:`~aircontrol.input_sink.BaseInputSink`, so the rules
about held buttons, held keys, and dry runs are shared rather than reimplemented
-- only the ``_emit_*`` hooks differ.

The module imports on every platform.  ``Quartz`` is loaded lazily, the first
time a sink actually needs to post an event, which keeps the test suite (and
Windows) free of a PyObjC dependency.  Tests inject a fake ``backend``.

Three things differ from Windows in ways worth knowing:

* **macOS has no relative mouse move.**  Quartz posts events at absolute
  screen points, so the sink reads the cursor position, applies the delta, and
  clamps to the display before posting.
* **Dragging is its own event type.**  Posting ``mouseMoved`` while the button
  is down does not drag on macOS -- most applications ignore it.  While the
  button is held, moves go out as ``leftMouseDragged`` instead.
* **Posting events needs permission.**  macOS gates synthetic input behind
  Accessibility permission (System Settings > Privacy & Security >
  Accessibility).  Without it Quartz accepts every event and silently discards
  it, which looks exactly like a broken gesture, so
  :func:`accessibility_trusted` lets callers check and say so plainly.
"""

from __future__ import annotations

import sys
from typing import Protocol

from aircontrol import mac_keymap
from aircontrol.input_sink import BaseInputSink, VK_LWIN, VK_SHIFT, VK_TAB


class MacEventBackend(Protocol):
    """The Quartz calls the sink needs; fakes implement these six methods."""

    def cursor_position(self) -> tuple[float, float]: ...

    def screen_bounds(self) -> tuple[float, float, float, float] | None: ...

    def move_cursor(self, x: float, y: float, *, dragging: bool) -> None: ...

    def mouse_button(self, x: float, y: float, *, down: bool) -> None: ...

    def scroll(self, lines: int) -> None: ...

    def key(self, keycode: int, *, down: bool, flags: int) -> None: ...

    def media_key(self, selector: int, *, down: bool) -> None: ...


def accessibility_trusted() -> bool:
    """Return whether this process may post synthetic input.

    False means macOS will swallow every event the sink posts.  Returns False
    on a non-macOS host, and if PyObjC is missing, since neither can inject.
    """
    try:
        from ApplicationServices import AXIsProcessTrusted  # type: ignore
    except Exception:
        return False
    try:
        return bool(AXIsProcessTrusted())
    except Exception:
        return False


class _QuartzBackend:
    """Thin wrapper around Quartz, instantiated lazily."""

    def __init__(self) -> None:
        if sys.platform != "darwin":
            raise OSError("macOS input injection is only available on macOS")

        try:
            import Quartz  # type: ignore
        except ImportError as exc:  # pragma: no cover - depends on the host
            raise OSError(
                "macOS input injection needs PyObjC (pyobjc-framework-Quartz). "
                "Reinstall AirControl to pick it up: python -m pip install -e ."
            ) from exc

        self._quartz = Quartz

    def cursor_position(self) -> tuple[float, float]:
        Quartz = self._quartz
        location = Quartz.CGEventGetLocation(Quartz.CGEventCreate(None))
        return float(location.x), float(location.y)

    def screen_bounds(self) -> tuple[float, float, float, float] | None:
        Quartz = self._quartz
        try:
            bounds = Quartz.CGDisplayBounds(Quartz.CGMainDisplayID())
        except Exception:  # pragma: no cover - depends on the host
            return None
        return (
            float(bounds.origin.x),
            float(bounds.origin.y),
            float(bounds.size.width),
            float(bounds.size.height),
        )

    def _post_mouse(self, event_type: int, x: float, y: float, button: int) -> None:
        Quartz = self._quartz
        event = Quartz.CGEventCreateMouseEvent(None, event_type, (x, y), button)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)

    def move_cursor(self, x: float, y: float, *, dragging: bool) -> None:
        Quartz = self._quartz
        event_type = (
            Quartz.kCGEventLeftMouseDragged if dragging else Quartz.kCGEventMouseMoved
        )
        self._post_mouse(event_type, x, y, Quartz.kCGMouseButtonLeft)

    def mouse_button(self, x: float, y: float, *, down: bool) -> None:
        Quartz = self._quartz
        event_type = (
            Quartz.kCGEventLeftMouseDown if down else Quartz.kCGEventLeftMouseUp
        )
        self._post_mouse(event_type, x, y, Quartz.kCGMouseButtonLeft)

    def scroll(self, lines: int) -> None:
        Quartz = self._quartz
        event = Quartz.CGEventCreateScrollWheelEvent(
            None, Quartz.kCGScrollEventUnitLine, 1, lines
        )
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)

    def key(self, keycode: int, *, down: bool, flags: int) -> None:
        Quartz = self._quartz
        event = Quartz.CGEventCreateKeyboardEvent(None, keycode, down)
        if flags:
            Quartz.CGEventSetFlags(event, flags)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)

    def media_key(self, selector: int, *, down: bool) -> None:
        # Volume and transport keys are NSSystemDefined events, not keystrokes.
        # subtype 8 is NX_SUBTYPE_AUX_CONTROL_BUTTONS; data1 packs the selector
        # in the high word and the key state in the low word.
        from AppKit import NSEvent  # type: ignore

        Quartz = self._quartz
        ns_system_defined = 14
        state = 0x0A if down else 0x0B
        event = NSEvent.otherEventWithType_location_modifierFlags_timestamp_windowNumber_context_subtype_data1_data2_(
            ns_system_defined,
            (0, 0),
            0xA00 if down else 0xB00,
            0,
            0,
            None,
            8,
            (selector << 16) | (state << 8),
            -1,
        )
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, event.CGEvent())


class MacInputSink(BaseInputSink):
    """Inject mouse and keyboard input on macOS through Quartz.

    ``backend`` is injectable for tests, exactly as on Windows.  ``dry_run``
    records requests without importing Quartz or touching the desktop.
    """

    def __init__(
        self,
        *,
        dry_run: bool = False,
        backend: MacEventBackend | None = None,
    ) -> None:
        super().__init__(dry_run=dry_run)
        self._backend = backend

    def _require_backend(self) -> MacEventBackend:
        if self._backend is None:
            self._backend = _QuartzBackend()
        return self._backend

    def _emit_move(self, dx: int, dy: int) -> None:
        backend = self._require_backend()
        x, y = backend.cursor_position()
        target_x, target_y = x + dx, y + dy
        bounds = backend.screen_bounds()
        if bounds is not None:
            left, top, width, height = bounds
            # Clamp a pixel inside the far edges: a point exactly on the
            # boundary can land on the neighbouring display or be discarded.
            target_x = min(max(target_x, left), left + width - 1)
            target_y = min(max(target_y, top), top + height - 1)
        backend.move_cursor(target_x, target_y, dragging=self._left_is_down)

    def _emit_left_down(self) -> None:
        backend = self._require_backend()
        x, y = backend.cursor_position()
        backend.mouse_button(x, y, down=True)

    def _emit_left_up(self) -> None:
        backend = self._require_backend()
        x, y = backend.cursor_position()
        backend.mouse_button(x, y, down=False)

    def _emit_scroll(self, notches: int) -> None:
        # Quartz scrolls in lines and already uses "positive is up", so the
        # notch count carries across without the Windows WHEEL_DELTA scaling.
        # macOS' "natural scrolling" setting applies to physical input devices,
        # not to synthetic scroll events, so direction matches Windows without
        # inverting anything here. Confirmed on hardware.
        self._require_backend().scroll(notches)

    def _emit_hotkey(self, virtual_key_codes: tuple[int, ...]) -> None:
        backend = self._require_backend()
        combo = mac_keymap.resolve_combo(virtual_key_codes)

        selector = mac_keymap.media_key(combo)
        if selector is not None:
            backend.media_key(selector, down=True)
            backend.media_key(selector, down=False)
            return

        modifiers, ordinary, flags = mac_keymap.split_modifiers(combo)
        # Resolve every keycode before posting anything: a chord that fails
        # halfway would leave modifiers physically held down.
        modifier_codes = [mac_keymap.modifier_keycode_for(key) for key in modifiers]
        ordinary_codes = [mac_keymap.keycode_for(key) for key in ordinary]

        # Modifiers go down first, each carrying the flags accumulated so far,
        # then the ordinary keys with the full set, then the modifiers back up
        # in reverse -- each release carrying what is still held.
        held = 0
        for key, keycode in zip(modifiers, modifier_codes):
            held |= mac_keymap.MODIFIER_FLAGS[key]
            backend.key(keycode, down=True, flags=held)
        for keycode in ordinary_codes:
            backend.key(keycode, down=True, flags=flags)
            backend.key(keycode, down=False, flags=flags)
        for key, keycode in zip(reversed(modifiers), reversed(modifier_codes)):
            held &= ~mac_keymap.MODIFIER_FLAGS[key]
            backend.key(keycode, down=False, flags=held)

    def _emit_key_releases(self, virtual_key_codes: tuple[int, ...]) -> None:
        backend = self._require_backend()
        for key in virtual_key_codes:
            keycode = mac_keymap.MODIFIER_KEYCODES.get(key)
            if keycode is None:
                keycode = mac_keymap.KEYCODES.get(key)
            if keycode is None:
                continue  # Nothing was pressed for it, so nothing to release.
            backend.key(keycode, down=False, flags=0)

    # -- window management ----------------------------------------------

    def switch_next(self) -> None:
        self.hotkey(VK_LWIN, VK_TAB)  # Cmd+Tab

    def switch_previous(self) -> None:
        self.hotkey(VK_LWIN, VK_SHIFT, VK_TAB)  # Cmd+Shift+Tab

    def overview(self) -> None:
        # The real Control key, not the Ctrl-means-Command mapping.
        self.hotkey(mac_keymap.VK_LCONTROL, mac_keymap.VK_UP)  # Mission Control

    def show_desktop(self) -> None:
        # F11 is the macOS default for Show Desktop, but on laptops the top row
        # defaults to brightness/volume, so bare F11 may do nothing unless
        # "Use F1, F2, etc. keys as standard function keys" is on. There is no
        # more reliable synthetic equivalent -- Mission Control's own shortcut
        # has the same problem -- so this is documented rather than worked
        # around. UNVERIFIED on hardware; see README "Known gaps".
        self.hotkey(mac_keymap.VK_F11)  # Show Desktop

    # The Windows-flavoured aliases, so callers can stay platform-agnostic.
    alt_tab = switch_next
    alt_shift_tab = switch_previous
    win_tab = overview
    win_d = show_desktop


__all__ = ["MacEventBackend", "MacInputSink", "accessibility_trusted"]
