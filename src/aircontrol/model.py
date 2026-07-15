from __future__ import annotations

import hashlib
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

from aircontrol.resources import is_frozen, resource_dir


MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
    "hand_landmarker/float16/1/hand_landmarker.task"
)
MINIMUM_MODEL_BYTES = 1_000_000
MODEL_SHA256 = "fbc2a30080c3c557093b5ddfc334698132eb341044ccee322ccf8bcf3607cde1"
BUNDLED_MODEL_PATH = Path("models/hand_landmarker.task")


class ModelDownloadError(RuntimeError):
    pass


def resolve_hand_model_path(
    path: str | Path,
    config_directory: str | Path,
) -> Path:
    """Resolve a model path against bundled assets or the source config."""
    model_path = Path(path)
    if model_path.is_absolute():
        return model_path
    base = (
        resource_dir()
        if is_frozen() and model_path == BUNDLED_MODEL_PATH
        else Path(config_directory)
    )
    return base / model_path


def _matches_official_model(path: Path) -> bool:
    if not path.exists() or path.stat().st_size < MINIMUM_MODEL_BYTES:
        return False
    digest = hashlib.sha256()
    with path.open("rb") as model_file:
        for chunk in iter(lambda: model_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest() == MODEL_SHA256


def ensure_hand_model(path: str | Path, progress: Callable[[str], None] | None = None) -> Path:
    model_path = Path(path)
    if _matches_official_model(model_path):
        return model_path

    model_path.parent.mkdir(parents=True, exist_ok=True)
    partial_path = model_path.with_suffix(".download")
    if progress:
        progress("Downloading the hand-tracking model (first run only)…")
    try:
        with urllib.request.urlopen(MODEL_URL, timeout=45) as response, partial_path.open("wb") as output:
            while chunk := response.read(1024 * 256):
                output.write(chunk)
        if not _matches_official_model(partial_path):
            raise ModelDownloadError("The downloaded hand model failed its integrity check")
        os.replace(partial_path, model_path)
    except ModelDownloadError:
        try:
            partial_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    except (OSError, urllib.error.URLError) as exc:
        try:
            partial_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise ModelDownloadError(
            "Could not download the MediaPipe hand model. Check the internet connection and run again."
        ) from exc
    return model_path
