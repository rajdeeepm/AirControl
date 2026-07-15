from __future__ import annotations

import pytest

from aircontrol.domain import ActionKind
from aircontrol.pipeline import action_category


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        (ActionKind.LEFT_DOWN, "click"),
        (ActionKind.LEFT_UP, "click"),
        (ActionKind.SCROLL, "scroll"),
        (ActionKind.SWITCH_NEXT, "window"),
        (ActionKind.SWITCH_PREVIOUS, "window"),
        (ActionKind.TASK_VIEW, "window"),
        (ActionKind.SHOW_DESKTOP, "window"),
        (ActionKind.MOVE_POINTER, "pointer"),
        (ActionKind.ESCAPE, "system"),
        (ActionKind.HOTKEY, "hotkey"),
    ],
)
def test_action_category_maps_every_action_kind(
    kind: ActionKind,
    expected: str,
) -> None:
    assert action_category(kind) == expected


@pytest.mark.parametrize(
    ("keys", "expected"),
    [
        ((0xAF,), "volume"),
        ((0xAE,), "volume"),
        ((0xAD,), "mute"),
        ((0xB3,), "media"),
        ((0xB0,), "media"),
        ((0xB1,), "media"),
        ((0x11, 0x43), "hotkey"),
        ((0x11, 0xAF), "hotkey"),
    ],
)
def test_action_category_maps_only_exact_curated_hotkeys(
    keys: tuple[int, ...],
    expected: str,
) -> None:
    assert action_category(ActionKind.HOTKEY, keys) == expected
