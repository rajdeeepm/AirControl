from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Callable

from aircontrol.resources import is_frozen, user_data_dir


CONSENT_FILE = ".aircontrol-consent.json"
CONSENT_VERSION = 1

NOTICE = """
AirControl privacy notice
-------------------------
Your camera frames and hand landmarks are processed on this laptop. MediaPipe
states that it does not send that input data to Google. MediaPipe Tasks 0.10.35
does send performance and API-utilization metrics to Google when a connection
is available. Those metrics are handled under Google's Privacy Policy.

AirControl never saves or uploads camera frames itself. You can review the
upstream notice at:
https://github.com/google-ai-edge/mediapipe#privacy-notice
""".strip()


class MetricsConsentDeclined(RuntimeError):
    pass


def _request_frozen_consent() -> bool:
    """Ask for consent without relying on a windowed executable's console."""
    if os.name != "nt":
        return False

    import ctypes

    message = (
        f"{NOTICE}\n\n"
        "Select Yes to accept this notice and continue, or No to cancel."
    )
    yes_no = 0x00000004
    information_icon = 0x00000040
    default_to_no = 0x00000100
    set_foreground = 0x00010000
    result = ctypes.windll.user32.MessageBoxW(  # type: ignore[attr-defined]
        None,
        message,
        "AirControl privacy notice",
        yes_no | information_icon | default_to_no | set_foreground,
    )
    return result == 6


def _consent_path(directory: str | Path) -> Path:
    base = user_data_dir() if is_frozen() else Path(directory)
    return base / CONSENT_FILE


def has_metrics_consent(directory: str | Path) -> bool:
    path = _consent_path(directory)
    if not path.exists():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return payload == {"accepted": True, "notice_version": CONSENT_VERSION}


def ensure_metrics_consent(
    directory: str | Path,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
) -> None:
    if has_metrics_consent(directory):
        return
    if is_frozen() and input_fn is input and output_fn is print:
        accepted = _request_frozen_consent()
    else:
        output_fn(NOTICE)
        try:
            response = input_fn("Type AGREE to continue, or press Enter to cancel: ")
        except EOFError:
            # No console to answer on -- launched by double-click, or stdin
            # closed. Say what to do instead of dying on a bare EOFError.
            raise MetricsConsentDeclined(
                "AirControl needs you to accept its privacy notice once, and "
                "there is no console here to accept it on. Start it from a "
                "terminal the first time, accept the notice, and afterwards it "
                "will launch normally."
            ) from None
        accepted = response.strip().upper() == "AGREE"
    if not accepted:
        raise MetricsConsentDeclined("MediaPipe metrics consent was not granted; AirControl did not start")
    path = _consent_path(directory)
    path.write_text(
        json.dumps({"accepted": True, "notice_version": CONSENT_VERSION}, indent=2) + "\n",
        encoding="utf-8",
    )
