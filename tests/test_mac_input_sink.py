"""macOS sink and keymap tests.

These run on every platform: the sink talks to a ``MacEventBackend``, and the
fake below records calls instead of posting them, so nothing here needs macOS,
PyObjC, or a desktop session.
"""

from __future__ import annotations

import pytest

from aircontrol import mac_keymap
from aircontrol.input_sink import VK_ESCAPE, VK_SHIFT, VK_TAB
from aircontrol.mac_input_sink import (
    MacInputSink,
    accessibility_trusted,
)


VK_CONTROL = mac_keymap.VK_CONTROL
VK_ALT = mac_keymap.VK_ALT
VK_LEFT = mac_keymap.VK_LEFT
VK_LWIN = mac_keymap.VK_LWIN
VK_T = ord("T")

KVK_COMMAND = mac_keymap.KVK_COMMAND
KVK_CONTROL = mac_keymap.KVK_CONTROL
KVK_SHIFT = mac_keymap.KVK_SHIFT
KVK_T = 0x11
CMD = mac_keymap.CG_FLAG_COMMAND
CTRL = mac_keymap.CG_FLAG_CONTROL
SHIFT = mac_keymap.CG_FLAG_SHIFT


class FakeBackend:
    """Records what the sink asked the OS to do."""

    def __init__(
        self,
        position: tuple[float, float] = (100.0, 100.0),
        bounds: tuple[float, float, float, float] | None = (0.0, 0.0, 1000.0, 800.0),
    ) -> None:
        self.position = position
        self.bounds = bounds
        self.calls: list[tuple] = []

    def cursor_position(self) -> tuple[float, float]:
        return self.position

    def screen_bounds(self):
        return self.bounds

    def move_cursor(self, x: float, y: float, *, dragging: bool) -> None:
        self.calls.append(("move", x, y, dragging))
        self.position = (x, y)

    def mouse_button(self, x: float, y: float, *, down: bool) -> None:
        self.calls.append(("button", x, y, down))

    def scroll(self, lines: int) -> None:
        self.calls.append(("scroll", lines))

    def key(self, keycode: int, *, down: bool, flags: int) -> None:
        self.calls.append(("key", keycode, down, flags))

    def media_key(self, selector: int, *, down: bool) -> None:
        self.calls.append(("media", selector, down))


def make_sink(**kwargs) -> tuple[MacInputSink, FakeBackend]:
    backend = FakeBackend(**kwargs)
    return MacInputSink(backend=backend), backend


# -- pointer ------------------------------------------------------------------


def test_relative_move_becomes_an_absolute_point() -> None:
    sink, backend = make_sink()

    sink.move_relative(30, -20)

    assert backend.calls == [("move", 130.0, 80.0, False)]


def test_move_while_the_button_is_held_drags() -> None:
    """Posting mouseMoved during a drag is ignored by most macOS apps."""
    sink, backend = make_sink()

    sink.left_down()
    backend.calls.clear()
    sink.move_relative(10, 10)

    assert backend.calls == [("move", 110.0, 110.0, True)]


def test_move_clamps_to_the_display() -> None:
    sink, backend = make_sink(position=(990.0, 10.0), bounds=(0.0, 0.0, 1000.0, 800.0))

    sink.move_relative(500, -500)

    assert backend.calls == [("move", 999.0, 0.0, False)]


def test_move_without_known_bounds_is_left_alone() -> None:
    sink, backend = make_sink(bounds=None)

    sink.move_relative(5_000, 5_000)

    assert backend.calls == [("move", 5100.0, 5100.0, False)]


def test_button_press_and_release_post_at_the_cursor() -> None:
    sink, backend = make_sink(position=(42.0, 24.0))

    sink.left_down()
    sink.left_up()

    assert backend.calls == [
        ("button", 42.0, 24.0, True),
        ("button", 42.0, 24.0, False),
    ]


def test_scroll_passes_notches_through_without_wheel_delta() -> None:
    """Quartz scrolls in lines; the Windows 120-unit scaling does not apply."""
    sink, backend = make_sink()

    sink.scroll_vertical(3)

    assert backend.calls == [("scroll", 3)]


# -- keyboard -----------------------------------------------------------------


def test_ctrl_shortcut_becomes_a_command_shortcut() -> None:
    sink, backend = make_sink()

    sink.hotkey(VK_CONTROL, VK_T)

    assert backend.calls == [
        ("key", KVK_COMMAND, True, CMD),
        ("key", KVK_T, True, CMD),
        ("key", KVK_T, False, CMD),
        ("key", KVK_COMMAND, False, 0),
    ]


def test_modifiers_release_carrying_what_is_still_held() -> None:
    sink, backend = make_sink()

    sink.hotkey(VK_LWIN, VK_SHIFT, VK_TAB)

    assert backend.calls == [
        ("key", KVK_COMMAND, True, CMD),
        ("key", KVK_SHIFT, True, CMD | SHIFT),
        ("key", mac_keymap.KVK_TAB, True, CMD | SHIFT),
        ("key", mac_keymap.KVK_TAB, False, CMD | SHIFT),
        ("key", KVK_SHIFT, False, CMD),
        ("key", KVK_COMMAND, False, 0),
    ]


def test_a_semantic_combination_is_rewritten_whole() -> None:
    """Alt+Left is browser-back on Windows; on macOS that is Cmd+Left."""
    sink, backend = make_sink()

    sink.hotkey(VK_ALT, VK_LEFT)

    assert backend.calls == [
        ("key", KVK_COMMAND, True, CMD),
        ("key", mac_keymap.KVK_LEFT, True, CMD),
        ("key", mac_keymap.KVK_LEFT, False, CMD),
        ("key", KVK_COMMAND, False, 0),
    ]


def test_a_media_key_uses_the_system_event_path() -> None:
    sink, backend = make_sink()

    sink.hotkey(mac_keymap.VK_MEDIA_PLAY_PAUSE)

    assert backend.calls == [
        ("media", mac_keymap.NX_KEYTYPE_PLAY, True),
        ("media", mac_keymap.NX_KEYTYPE_PLAY, False),
    ]


def test_a_media_key_inside_a_chord_is_refused_rather_than_guessed_at() -> None:
    """macOS has no "Cmd + play/pause" chord, so the sink says so instead of
    inventing one. Failing loudly beats posting a keystroke that does something
    else."""
    sink, backend = make_sink()

    with pytest.raises(mac_keymap.UntranslatableKey):
        sink.hotkey(VK_CONTROL, mac_keymap.VK_MEDIA_PLAY_PAUSE)

    assert not any(call[0] == "media" for call in backend.calls)
    assert not any(call[0] == "key" and call[2] is True for call in backend.calls)


def test_escape_translates() -> None:
    sink, backend = make_sink()

    sink.hotkey(VK_ESCAPE)

    assert backend.calls == [
        ("key", mac_keymap.KVK_ESCAPE, True, 0),
        ("key", mac_keymap.KVK_ESCAPE, False, 0),
    ]


def test_an_untranslatable_key_presses_nothing() -> None:
    """Every keycode resolves before the first event is posted, so a chord that
    cannot be translated never half-presses.

    The base sink still posts defensive key-ups afterwards, as it does on
    Windows: a key-up for a key that was never pressed is a no-op, and the
    alternative is risking a modifier stuck down."""
    sink, backend = make_sink()

    with pytest.raises(mac_keymap.UntranslatableKey):
        sink.hotkey(VK_CONTROL, 0xFF)

    presses = [call for call in backend.calls if call[0] == "key" and call[2] is True]
    assert presses == []


# -- window management --------------------------------------------------------


def test_switch_next_is_command_tab() -> None:
    sink, backend = make_sink()

    sink.switch_next()

    assert backend.calls[0] == ("key", KVK_COMMAND, True, CMD)
    assert ("key", mac_keymap.KVK_TAB, True, CMD) in backend.calls


def test_overview_uses_the_real_control_key_not_command() -> None:
    """Mission Control is Control+Up; the Ctrl-means-Command rule must not apply."""
    sink, backend = make_sink()

    sink.overview()

    assert backend.calls == [
        ("key", KVK_CONTROL, True, CTRL),
        ("key", mac_keymap.KVK_UP, True, CTRL),
        ("key", mac_keymap.KVK_UP, False, CTRL),
        ("key", KVK_CONTROL, False, 0),
    ]


def test_show_desktop_is_f11() -> None:
    sink, backend = make_sink()

    sink.show_desktop()

    assert backend.calls == [
        ("key", 0x67, True, 0),
        ("key", 0x67, False, 0),
    ]


def test_the_windows_named_aliases_still_work() -> None:
    sink, backend = make_sink()

    sink.win_tab()

    assert backend.calls[0] == ("key", KVK_CONTROL, True, CTRL)


# -- shared bookkeeping, inherited from BaseInputSink -------------------------


def test_dry_run_records_without_posting() -> None:
    sink = MacInputSink(dry_run=True, backend=None)

    sink.move_relative(5, 5)
    sink.left_down()
    sink.hotkey(VK_CONTROL, VK_T)

    # No backend was ever needed, so none was constructed.
    assert sink._backend is None
    assert sink.descriptions == (
        "Move pointer by (+5, +5) px",
        "Left button down",
        "Hotkey: VK_0x11 + VK_0x54",
    )


def test_release_all_releases_a_held_button() -> None:
    sink, backend = make_sink()

    sink.left_down()
    backend.calls.clear()
    sink.release_all()

    assert backend.calls == [("button", 100.0, 100.0, False)]
    assert sink.left_is_down is False


def test_closing_releases_a_drag() -> None:
    sink, backend = make_sink()

    with sink:
        sink.left_down()
        backend.calls.clear()

    assert backend.calls == [("button", 100.0, 100.0, False)]


def test_switching_to_dry_run_does_not_strand_the_button() -> None:
    sink, backend = make_sink()

    sink.left_down()
    backend.calls.clear()
    sink.set_dry_run(True)

    assert backend.calls == [("button", 100.0, 100.0, False)]


# -- keymap -------------------------------------------------------------------


def test_letters_and_digits_map_to_their_mac_keycodes() -> None:
    assert mac_keymap.keycode_for(ord("A")) == 0x00
    assert mac_keymap.keycode_for(ord("Z")) == 0x06
    assert mac_keymap.keycode_for(ord("0")) == 0x1D


def test_function_keys_map_across_their_scattered_codes() -> None:
    assert mac_keymap.keycode_for(0x70) == 0x7A  # F1
    assert mac_keymap.keycode_for(0x7A) == 0x67  # F11


def test_an_unmapped_code_raises_rather_than_guessing() -> None:
    with pytest.raises(mac_keymap.UntranslatableKey):
        mac_keymap.keycode_for(0xFF)


def test_split_modifiers_separates_and_accumulates_flags() -> None:
    modifiers, ordinary, flags = mac_keymap.split_modifiers((VK_CONTROL, VK_SHIFT, VK_T))

    assert modifiers == (VK_CONTROL, VK_SHIFT)
    assert ordinary == (VK_T,)
    assert flags == CMD | SHIFT


def test_every_curated_ui_hotkey_translates() -> None:
    """The action catalogue in ui/src/lib/actions.ts must fully translate."""
    curated = (
        (179,), (176,), (177,), (175,), (174,), (173,),  # media
        (91, 44),                                        # screenshot
        (33,), (34,),                                    # page up/down
        (18, 37), (18, 39),                              # browser back/forward
        (116,),                                          # refresh
        (17, 84), (17, 87),                              # new/close tab
    )
    for combo in curated:
        resolved = mac_keymap.resolve_combo(combo)
        if mac_keymap.media_key(resolved) is not None:
            continue
        _, ordinary, _ = mac_keymap.split_modifiers(resolved)
        for key in ordinary:
            mac_keymap.keycode_for(key)  # raises if the catalogue outgrows the map


def test_accessibility_is_not_trusted_off_macos() -> None:
    import sys

    if sys.platform == "darwin":
        pytest.skip("on macOS the answer depends on the host's granted permissions")
    assert accessibility_trusted() is False
