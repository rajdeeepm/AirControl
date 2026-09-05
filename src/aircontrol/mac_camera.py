"""macOS camera permission, reported in terms a user can act on.

Windows camera permission is a toggle: find AirControl in Settings, switch it
on.  macOS works differently in three ways that each produce a confusing
failure, and OpenCV reports none of them -- it returns the same "could not
return a frame" whether the camera is busy, missing, or forbidden.

1. **Permission belongs to the app that launched AirControl**, not to
   AirControl.  Running ``./app.sh`` from a terminal, the camera prompt goes to
   *that terminal*, and the entry that appears under System Settings > Privacy
   & Security > Camera is Terminal or iTerm -- there is no "AirControl" row to
   toggle, and people reasonably conclude the permission is missing.
2. **A denial is permanent.**  macOS asks once.  Dismiss or deny it and the
   system never asks again; it simply fails from then on.  Re-granting means
   finding the launching app in System Settings, or ``tccutil reset Camera``.
3. **Authorized is not the same as working.**  A capture device can be
   authorized, connected, idle, and still deliver no frames -- when another
   process holds it, or when the launching process is not one macOS will start
   a capture session for.

This module answers "which of those is it" so the error message can say so.
Every function is safe to call on any platform and never raises.
"""

from __future__ import annotations

import sys


AUTHORIZED = "authorized"
DENIED = "denied"
RESTRICTED = "restricted"
NOT_DETERMINED = "not_determined"
UNKNOWN = "unknown"

# AVAuthorizationStatus, from AVFoundation.
_STATUS_NAMES = {
    0: NOT_DETERMINED,
    1: RESTRICTED,
    2: DENIED,
    3: AUTHORIZED,
}


def is_macos() -> bool:
    return sys.platform == "darwin"


def camera_authorization() -> str:
    """Return this process's macOS camera authorization.

    ``UNKNOWN`` on any non-macOS host, and when PyObjC cannot answer -- callers
    treat that as "carry on and let the capture attempt speak for itself".
    """
    if not is_macos():
        return UNKNOWN
    try:
        import AVFoundation  # type: ignore
    except Exception:
        return UNKNOWN
    try:
        status = AVFoundation.AVCaptureDevice.authorizationStatusForMediaType_(
            AVFoundation.AVMediaTypeVideo
        )
    except Exception:
        return UNKNOWN
    return _STATUS_NAMES.get(int(status), UNKNOWN)


def camera_names() -> tuple[str, ...]:
    """Return the video devices macOS can see, for a more specific message."""
    if not is_macos():
        return ()
    try:
        import AVFoundation  # type: ignore

        device = AVFoundation.AVCaptureDevice.defaultDeviceWithMediaType_(
            AVFoundation.AVMediaTypeVideo
        )
    except Exception:
        return ()
    if device is None:
        return ()
    try:
        return (str(device.localizedName()),)
    except Exception:
        return ()


_SETTINGS_PATH = "System Settings > Privacy & Security > Camera"


def permission_hint() -> str | None:
    """Explain a macOS camera problem, or return None if permission is fine.

    The wording names the launching application deliberately: the single most
    common wrong turn is looking for an "AirControl" row that will never exist.
    """
    if not is_macos():
        return None

    status = camera_authorization()

    if status == NOT_DETERMINED:
        return (
            "macOS has not yet been asked for camera access. It will prompt "
            "once -- and the prompt names the application that launched "
            "AirControl (your terminal, if you ran ./app.sh there), not "
            "AirControl itself. Accept it. If no prompt appears, launch "
            "AirControl from a terminal you have used interactively."
        )

    if status == DENIED:
        return (
            "macOS is refusing camera access, and it will not ask again -- it "
            f"only ever asks once. Open {_SETTINGS_PATH} and switch on the "
            "application you launch AirControl from (Terminal, iTerm, or your "
            "editor); there is no AirControl entry, because permission belongs "
            "to the launching app. If it is not listed, reset the prompt with "
            "'tccutil reset Camera' and start AirControl again."
        )

    if status == RESTRICTED:
        return (
            "Camera access is restricted on this Mac by a profile or parental "
            "controls, so AirControl cannot be granted it. Check with whoever "
            "manages the device."
        )

    if status == AUTHORIZED:
        return None

    return None


def authorized_but_no_frames_hint() -> str:
    """Explain a camera that is authorized and open yet delivers nothing."""
    devices = camera_names()
    seen = f" macOS reports '{devices[0]}' present." if devices else ""
    return (
        "The camera was opened but never delivered a frame, even though macOS "
        f"reports access is authorized.{seen} Usually another application still "
        "holds the camera -- quit Zoom, FaceTime, Photo Booth, or a browser "
        "tab using it, including one that only appears closed. If nothing else "
        "is using it, launching AirControl from a terminal window (rather than "
        "an editor, task runner, or remote session) lets macOS start a capture "
        "session for it. To pick a different camera, set camera.index in "
        "config.json."
    )


__all__ = [
    "AUTHORIZED",
    "DENIED",
    "NOT_DETERMINED",
    "RESTRICTED",
    "UNKNOWN",
    "authorized_but_no_frames_hint",
    "camera_authorization",
    "camera_names",
    "is_macos",
    "permission_hint",
]
