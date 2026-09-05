"""macOS camera permission reporting.

Runs on every platform: the AVFoundation lookup is patched, so nothing here
needs macOS or PyObjC.
"""

from __future__ import annotations

import pytest

from aircontrol import mac_camera


@pytest.fixture
def on_macos(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(mac_camera, "is_macos", lambda: True)


def _status(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setattr(mac_camera, "camera_authorization", lambda: value)


def test_nothing_to_say_off_macos(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mac_camera, "is_macos", lambda: False)
    assert mac_camera.permission_hint() is None
    assert mac_camera.camera_authorization() == mac_camera.UNKNOWN


def test_authorized_produces_no_hint(on_macos, monkeypatch) -> None:
    _status(monkeypatch, mac_camera.AUTHORIZED)
    assert mac_camera.permission_hint() is None


def test_denied_says_macos_will_not_ask_again(on_macos, monkeypatch) -> None:
    """The sticky denial is the part people do not expect."""
    _status(monkeypatch, mac_camera.DENIED)
    hint = mac_camera.permission_hint()
    assert hint is not None
    assert "will not ask again" in hint
    assert "tccutil reset Camera" in hint


def test_denied_explains_there_is_no_aircontrol_entry(on_macos, monkeypatch) -> None:
    """Permission belongs to the launching app, which is the confusing part."""
    _status(monkeypatch, mac_camera.DENIED)
    hint = mac_camera.permission_hint()
    assert "no AirControl entry" in hint
    assert "Privacy & Security > Camera" in hint


def test_not_determined_warns_the_prompt_names_the_terminal(on_macos, monkeypatch) -> None:
    _status(monkeypatch, mac_camera.NOT_DETERMINED)
    hint = mac_camera.permission_hint()
    assert hint is not None
    assert "launched AirControl" in hint


def test_restricted_points_at_device_management(on_macos, monkeypatch) -> None:
    _status(monkeypatch, mac_camera.RESTRICTED)
    hint = mac_camera.permission_hint()
    assert hint is not None
    assert "restricted" in hint.lower()


def test_unknown_stays_quiet_and_lets_the_capture_speak(on_macos, monkeypatch) -> None:
    """If we cannot tell, do not invent a permissions problem."""
    _status(monkeypatch, mac_camera.UNKNOWN)
    assert mac_camera.permission_hint() is None


def test_missing_pyobjc_is_unknown_not_an_error(on_macos, monkeypatch) -> None:
    """A machine without PyObjC must not crash on the way to a camera error."""
    import builtins

    real_import = builtins.__import__

    def no_avfoundation(name, *args, **kwargs):
        if name == "AVFoundation":
            raise ImportError("no AVFoundation here")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_avfoundation)
    assert mac_camera.camera_authorization() == mac_camera.UNKNOWN


def test_no_frames_hint_separates_busy_from_forbidden(on_macos, monkeypatch) -> None:
    monkeypatch.setattr(mac_camera, "camera_names", lambda: ("MacBook Pro Camera",))
    hint = mac_camera.authorized_but_no_frames_hint()
    assert "authorized" in hint
    assert "MacBook Pro Camera" in hint
    assert "holds the camera" in hint


def test_no_frames_hint_survives_an_unnameable_device(on_macos, monkeypatch) -> None:
    monkeypatch.setattr(mac_camera, "camera_names", lambda: ())
    assert "authorized" in mac_camera.authorized_but_no_frames_hint()
