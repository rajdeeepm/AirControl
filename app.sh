#!/usr/bin/env bash
# Open the live AirControl desktop app on macOS. The Windows equivalent is app.cmd.
# Any arguments are passed through, so ./app.sh --practice runs without
# sending real input.
set -euo pipefail
cd "$(dirname "$0")"

needs_setup() {
  [ -x ".venv/bin/python" ] || return 0
  [ -d "ui/dist" ] || return 0
  .venv/bin/python - <<'PY' >/dev/null 2>&1 || return 0
import importlib.util as u, sys
sys.exit(0 if all(u.find_spec(n) for n in ("aircontrol", "cv2", "mediapipe", "websockets", "webview")) else 1)
PY
  return 1
}

if needs_setup; then
  ./setup.sh
fi

exec .venv/bin/python -m aircontrol --app "$@"
