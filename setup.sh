#!/usr/bin/env bash
# One-time setup for a fresh clone on macOS: Python environment, UI bundle,
# hand model. The Windows equivalent is setup.cmd.
set -euo pipefail
cd "$(dirname "$0")"

fail() { printf '\n%s\n' "$1" >&2; exit 1; }

if [ "$(uname -s)" != "Darwin" ]; then
  fail "setup.sh is the macOS setup script. On Windows, run setup.cmd instead."
fi

# Find the newest Python >= 3.11. Version-suffixed names are tried newest
# first so a machine with several installs gets the best one, and a few common
# install roots are searched directly: Homebrew's bin is often missing from a
# non-interactive PATH, and "python3" alone may be an old system Python or an
# unrelated virtualenv that happens to be active.
find_python() {
  local minor name dir bin
  local dirs=("" "/opt/homebrew/bin/" "/usr/local/bin/")
  local names=()
  for minor in $(seq 30 -1 11); do names+=("python3.$minor"); done
  names+=("python3")
  for name in "${names[@]}"; do
    for dir in "${dirs[@]}"; do
      if [ -n "$dir" ]; then
        bin="$dir$name"
        [ -x "$bin" ] || continue
      else
        bin="$(command -v "$name" 2>/dev/null)" || continue
      fi
      if "$bin" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
        printf '%s' "$bin"
        return 0
      fi
    done
  done
  return 1
}

python_bin="$(find_python)" || fail "AirControl needs Python 3.11 or newer.
Install it with Homebrew (brew install python@3.12) or from python.org,
then run this script again."
echo "Using $python_bin ($("$python_bin" -V 2>&1))"

command -v npm >/dev/null 2>&1 || fail "AirControl needs Node.js to build its desktop UI.
Install the LTS build from https://nodejs.org/en/download (or brew install node),
then run this script again."

if [ ! -x ".venv/bin/python" ]; then
  echo "[1/4] Creating AirControl's private Python environment..."
  "$python_bin" -m venv .venv
else
  echo "[1/4] Python environment already exists."
fi

echo "[2/4] Installing AirControl..."
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e ".[dev]"

echo "[3/4] Building the desktop UI..."
npm --prefix ui ci || npm --prefix ui install
npm --prefix ui run build
[ -d "ui/dist" ] || fail "The desktop UI did not build. Check the message above, then try again."

echo "[4/4] Downloading the hand-tracking model (first run only)..."
.venv/bin/python -c "from aircontrol.model import ensure_hand_model; ensure_hand_model('models/hand_landmarker.task', progress=print)"

cat <<'DONE'

AirControl is ready. Start it with ./app.sh

One more step the first time you run it: macOS will not let any app move your
pointer until you allow it. When AirControl first tries, grant it under
System Settings > Privacy & Security > Accessibility, then restart the app.
Practice mode (./app.sh --practice) needs no permission at all.
DONE
