from aircontrol.controller import ActionController
from aircontrol.domain import Action, ActionKind


def test_controller_scales_relative_pointer_motion_in_practice_mode():
    controller = ActionController(pointer_pixels_per_palm=800, practice=True)

    assert controller.dispatch(Action(ActionKind.MOVE_POINTER, dx=0.1, dy=-0.05)) is None

    event = controller.sink.events[-1]
    assert event.kind == "move_relative"
    assert event.values == (80, -40)


def test_controller_maps_window_actions_to_complete_hotkeys():
    controller = ActionController(pointer_pixels_per_palm=800, practice=True)

    description = controller.dispatch(Action(ActionKind.SWITCH_PREVIOUS))

    assert description == "PREVIOUS APP"
    assert controller.sink.events[-1].description == "Hotkey: Alt + Shift + Tab"


def test_unknown_action_is_rejected():
    controller = ActionController(pointer_pixels_per_palm=800, practice=True)
    action = Action.__new__(Action)
    object.__setattr__(action, "kind", "not-real")
    object.__setattr__(action, "dx", 0.0)
    object.__setattr__(action, "dy", 0.0)
    object.__setattr__(action, "amount", 0)

    try:
        controller.dispatch(action)
    except ValueError as exc:
        assert "Unsupported action" in str(exc)
    else:
        raise AssertionError("Unsupported action should fail")

