"""Translate Windows virtual-key codes into macOS key events.

AirControl uses Windows virtual-key codes as its portable currency for keys:
the UI's action catalogue emits them, the SQLite store persists them, and the
IPC layer passes them through untouched.  That keeps every layer above the sink
platform-independent, and confines "what key is this really" to one place --
this module.

Three kinds of translation happen here, in priority order:

1. **Media keys.**  Volume and transport keys are not ordinary keystrokes on
   macOS.  They travel as ``NSSystemDefined`` events carrying an ``NX_KEYTYPE_*``
   selector, so they are mapped separately and emitted down a different path.
2. **Semantic combinations.**  A few shortcuts have no literal macOS
   counterpart.  Browser back is ``Alt+Left`` on Windows and ``Cmd+Left`` on
   macOS; refresh is ``F5`` versus ``Cmd+R``.  Translating those key-for-key
   would produce a working keystroke that does the wrong thing, which is worse
   than failing, so they are rewritten as whole combinations.
3. **Literal keys.**  Everything else maps code-for-code.

**Control becomes Command.**  Windows' Ctrl and macOS' Command occupy the same
role -- they are the modifier that application shortcuts hang off -- so Ctrl+T,
Ctrl+W, Ctrl+C and friends land as Cmd+T, Cmd+W, Cmd+C and do what the user
meant.  The alternative, mapping Ctrl to macOS Control, is literally faithful
and almost always wrong: Cmd+C copies, Control+C does not.  The cost is that
Windows' text-navigation chords (Ctrl+Left to jump a word) land on macOS'
line-navigation chords instead.  That trade is deliberate, and it is why the
semantic table above exists to catch the cases where it misfires.
"""

from __future__ import annotations


# -- Windows virtual-key codes we translate from ------------------------------

VK_BACK = 0x08
VK_TAB = 0x09
VK_RETURN = 0x0D
VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_ALT = 0x12
VK_ESCAPE = 0x1B
VK_SPACE = 0x20
VK_PAGE_UP = 0x21
VK_PAGE_DOWN = 0x22
VK_END = 0x23
VK_HOME = 0x24
VK_LEFT = 0x25
VK_UP = 0x26
VK_RIGHT = 0x27
VK_DOWN = 0x28
VK_SNAPSHOT = 0x2C
VK_DELETE = 0x2E
VK_LWIN = 0x5B
VK_RWIN = 0x5C
VK_F5 = 0x74
VK_F11 = 0x7A
# Windows distinguishes the generic Ctrl (0x11) from the physical left
# Control key.  We use that: generic Ctrl means "the shortcut modifier"
# and becomes Command, while VK_LCONTROL means the real Control key, which
# macOS needs for Mission Control and friends.
VK_LCONTROL = 0xA2

VK_VOLUME_MUTE = 0xAD
VK_VOLUME_DOWN = 0xAE
VK_VOLUME_UP = 0xAF
VK_MEDIA_NEXT_TRACK = 0xB0
VK_MEDIA_PREV_TRACK = 0xB1
VK_MEDIA_PLAY_PAUSE = 0xB3


# -- macOS virtual keycodes (Carbon HIToolbox ``Events.h``) -------------------

KVK_RETURN = 0x24
KVK_TAB = 0x30
KVK_SPACE = 0x31
KVK_DELETE = 0x33
KVK_ESCAPE = 0x35
KVK_COMMAND = 0x37
KVK_SHIFT = 0x38
KVK_OPTION = 0x3A
KVK_CONTROL = 0x3B
KVK_HOME = 0x73
KVK_PAGE_UP = 0x74
KVK_FORWARD_DELETE = 0x75
KVK_END = 0x77
KVK_PAGE_DOWN = 0x79
KVK_LEFT = 0x7B
KVK_RIGHT = 0x7C
KVK_DOWN = 0x7D
KVK_UP = 0x7E

# Letters are not contiguous on a macOS keyboard the way VK codes are.
_LETTER_KEYCODES = {
    "A": 0x00, "B": 0x0B, "C": 0x08, "D": 0x02, "E": 0x0E, "F": 0x03,
    "G": 0x05, "H": 0x04, "I": 0x22, "J": 0x26, "K": 0x28, "L": 0x25,
    "M": 0x2E, "N": 0x2D, "O": 0x1F, "P": 0x23, "Q": 0x0C, "R": 0x0F,
    "S": 0x01, "T": 0x11, "U": 0x20, "V": 0x09, "W": 0x0D, "X": 0x07,
    "Y": 0x10, "Z": 0x06,
}
_DIGIT_KEYCODES = {
    "0": 0x1D, "1": 0x12, "2": 0x13, "3": 0x14, "4": 0x15,
    "5": 0x17, "6": 0x16, "7": 0x1A, "8": 0x1C, "9": 0x19,
}
# F-key codes are scattered too, so they are listed rather than computed.
_FUNCTION_KEYCODES = {
    1: 0x7A, 2: 0x78, 3: 0x63, 4: 0x76, 5: 0x60, 6: 0x61,
    7: 0x62, 8: 0x64, 9: 0x65, 10: 0x6D, 11: 0x67, 12: 0x6F,
}


# -- CGEventFlags -------------------------------------------------------------

CG_FLAG_SHIFT = 0x00020000
CG_FLAG_CONTROL = 0x00040000
CG_FLAG_OPTION = 0x00080000
CG_FLAG_COMMAND = 0x00100000


# -- NX_KEYTYPE_* selectors (IOKit ``ev_keymap.h``) ---------------------------

NX_KEYTYPE_SOUND_UP = 0
NX_KEYTYPE_SOUND_DOWN = 1
NX_KEYTYPE_MUTE = 7
NX_KEYTYPE_PLAY = 16
NX_KEYTYPE_NEXT = 17
NX_KEYTYPE_PREVIOUS = 18


# -- The tables ---------------------------------------------------------------

#: Media and volume keys, which travel as system-defined events, not keystrokes.
MEDIA_KEYS: dict[int, int] = {
    VK_VOLUME_UP: NX_KEYTYPE_SOUND_UP,
    VK_VOLUME_DOWN: NX_KEYTYPE_SOUND_DOWN,
    VK_VOLUME_MUTE: NX_KEYTYPE_MUTE,
    VK_MEDIA_PLAY_PAUSE: NX_KEYTYPE_PLAY,
    VK_MEDIA_NEXT_TRACK: NX_KEYTYPE_NEXT,
    VK_MEDIA_PREV_TRACK: NX_KEYTYPE_PREVIOUS,
}

#: Modifier keys, which set an event flag rather than producing a character.
#: Both Ctrl and the Windows key become Command -- see the module docstring.
MODIFIER_FLAGS: dict[int, int] = {
    VK_SHIFT: CG_FLAG_SHIFT,
    VK_CONTROL: CG_FLAG_COMMAND,
    VK_LCONTROL: CG_FLAG_CONTROL,
    VK_ALT: CG_FLAG_OPTION,
    VK_LWIN: CG_FLAG_COMMAND,
    VK_RWIN: CG_FLAG_COMMAND,
}

#: The keycode a held modifier posts, so a chord's modifiers go down and up.
MODIFIER_KEYCODES: dict[int, int] = {
    VK_SHIFT: KVK_SHIFT,
    VK_CONTROL: KVK_COMMAND,
    VK_LCONTROL: KVK_CONTROL,
    VK_ALT: KVK_OPTION,
    VK_LWIN: KVK_COMMAND,
    VK_RWIN: KVK_COMMAND,
}


def _build_keycodes() -> dict[int, int]:
    codes: dict[int, int] = {
        VK_BACK: KVK_DELETE,
        VK_TAB: KVK_TAB,
        VK_RETURN: KVK_RETURN,
        VK_ESCAPE: KVK_ESCAPE,
        VK_SPACE: KVK_SPACE,
        VK_PAGE_UP: KVK_PAGE_UP,
        VK_PAGE_DOWN: KVK_PAGE_DOWN,
        VK_END: KVK_END,
        VK_HOME: KVK_HOME,
        VK_LEFT: KVK_LEFT,
        VK_UP: KVK_UP,
        VK_RIGHT: KVK_RIGHT,
        VK_DOWN: KVK_DOWN,
        VK_DELETE: KVK_FORWARD_DELETE,
    }
    for letter, keycode in _LETTER_KEYCODES.items():
        codes[ord(letter)] = keycode  # VK_A..VK_Z are the ASCII codes.
    for digit, keycode in _DIGIT_KEYCODES.items():
        codes[ord(digit)] = keycode  # VK_0..VK_9 likewise.
    for number, keycode in _FUNCTION_KEYCODES.items():
        codes[0x70 + number - 1] = keycode  # VK_F1 is 0x70.
    return codes


#: Windows virtual-key code -> macOS virtual keycode, for ordinary keys.
KEYCODES: dict[int, int] = _build_keycodes()


#: Combinations whose literal translation would do the wrong thing on macOS.
#: Each value is itself expressed in Windows virtual-key codes, so it flows back
#: through the literal tables above; ``VK_LWIN`` is how "Command" is spelled.
SEMANTIC_COMBOS: dict[frozenset[int], tuple[int, ...]] = {
    # Browser back/forward: Alt+Arrow on Windows, Cmd+Arrow on macOS.
    frozenset({VK_ALT, VK_LEFT}): (VK_LWIN, VK_LEFT),
    frozenset({VK_ALT, VK_RIGHT}): (VK_LWIN, VK_RIGHT),
    # Refresh: F5 on Windows, Cmd+R on macOS.
    frozenset({VK_F5}): (VK_LWIN, ord("R")),
    # Screenshot: Win+PrintScreen on Windows, Cmd+Shift+3 on macOS.
    frozenset({VK_LWIN, VK_SNAPSHOT}): (VK_LWIN, VK_SHIFT, ord("3")),
}


class UntranslatableKey(LookupError):
    """Raised when a virtual-key code has no macOS equivalent."""


def resolve_combo(virtual_key_codes: tuple[int, ...]) -> tuple[int, ...]:
    """Apply the semantic rewrite, if this combination has one."""
    return SEMANTIC_COMBOS.get(frozenset(virtual_key_codes), virtual_key_codes)


def media_key(virtual_key_codes: tuple[int, ...]) -> int | None:
    """Return the ``NX_KEYTYPE_*`` selector for a lone media key, else ``None``.

    Media keys are only meaningful on their own; a chord containing one is not
    a media command, so it falls through to ordinary translation.
    """
    if len(virtual_key_codes) != 1:
        return None
    return MEDIA_KEYS.get(virtual_key_codes[0])


def split_modifiers(
    virtual_key_codes: tuple[int, ...],
) -> tuple[tuple[int, ...], tuple[int, ...], int]:
    """Split a combination into (modifier VKs, ordinary VKs, combined flags)."""
    modifiers: list[int] = []
    ordinary: list[int] = []
    flags = 0
    for code in virtual_key_codes:
        flag = MODIFIER_FLAGS.get(code)
        if flag is None:
            ordinary.append(code)
        else:
            modifiers.append(code)
            flags |= flag
    return tuple(modifiers), tuple(ordinary), flags


def keycode_for(virtual_key_code: int) -> int:
    """Return the macOS keycode for an ordinary Windows virtual-key code."""
    try:
        return KEYCODES[virtual_key_code]
    except KeyError:
        raise UntranslatableKey(
            f"no macOS key matches Windows virtual-key code 0x{virtual_key_code:02X}"
        ) from None


def modifier_keycode_for(virtual_key_code: int) -> int:
    """Return the macOS keycode a held modifier posts."""
    try:
        return MODIFIER_KEYCODES[virtual_key_code]
    except KeyError:
        raise UntranslatableKey(
            f"0x{virtual_key_code:02X} is not a modifier"
        ) from None


__all__ = [
    "CG_FLAG_COMMAND",
    "CG_FLAG_CONTROL",
    "CG_FLAG_OPTION",
    "CG_FLAG_SHIFT",
    "KEYCODES",
    "MEDIA_KEYS",
    "MODIFIER_FLAGS",
    "MODIFIER_KEYCODES",
    "SEMANTIC_COMBOS",
    "UntranslatableKey",
    "keycode_for",
    "media_key",
    "modifier_keycode_for",
    "resolve_combo",
    "split_modifiers",
]
