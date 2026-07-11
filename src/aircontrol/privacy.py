from __future__ import annotations

import json
from pathlib import Path
from typing import Callable


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


def _consent_path(directory: str | Path) -> Path:
    return Path(directory) / CONSENT_FILE


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
    output_fn(NOTICE)
    response = input_fn("Type AGREE to continue, or press Enter to cancel: ")
    if response.strip().upper() != "AGREE":
        raise MetricsConsentDeclined("MediaPipe metrics consent was not granted; AirControl did not start")
    path = _consent_path(directory)
    path.write_text(
        json.dumps({"accepted": True, "notice_version": CONSENT_VERSION}, indent=2) + "\n",
        encoding="utf-8",
    )

