@echo off
rem One-time setup for a fresh clone: Python environment, UI bundle, hand model.
setlocal
cd /d "%~dp0"

where py >nul 2>&1
if errorlevel 1 goto :python_missing
py -3 -c "import platform,sys; ok=sys.version_info >= (3,11) and sys.maxsize > 2**32 and platform.machine().lower() in ('amd64','x86_64'); sys.exit(0 if ok else 1)" >nul 2>&1
if errorlevel 1 goto :python_missing

where npm >nul 2>&1
if errorlevel 1 goto :node_missing

if not exist ".venv\Scripts\python.exe" (
  echo [1/4] Creating AirControl's private Python environment...
  py -3 -m venv .venv
  if errorlevel 1 goto :failed
) else (
  echo [1/4] Python environment already exists.
)

echo [2/4] Installing AirControl...
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto :failed
".venv\Scripts\python.exe" -m pip install -e ".[dev]"
if errorlevel 1 goto :failed

echo [3/4] Building the desktop UI...
call npm --prefix ui ci
if errorlevel 1 call npm --prefix ui install
if errorlevel 1 goto :ui_failed
call npm --prefix ui run build
if errorlevel 1 goto :ui_failed
if not exist "ui\dist\" goto :ui_failed

echo [4/4] Downloading the hand-tracking model (first run only)...
".venv\Scripts\python.exe" -c "from aircontrol.model import ensure_hand_model; ensure_hand_model('models/hand_landmarker.task', progress=print)"
if errorlevel 1 goto :model_failed

echo.
echo AirControl is ready. Double-click app.cmd to start.
exit /b 0

:python_missing
echo.
echo AirControl needs x64 Python 3.11 or newer from python.org.
echo Install it with the Python Launcher option, then run this file again.
echo https://www.python.org/downloads/windows/
pause
exit /b 1

:node_missing
echo.
echo AirControl needs Node.js to build its desktop UI.
echo Install the LTS build, reopen this window, then run this file again.
echo https://nodejs.org/en/download
pause
exit /b 1

:ui_failed
echo.
echo The desktop UI did not build. Check the message above, then try again.
echo You can retry just this step with:
echo   npm --prefix ui install ^&^& npm --prefix ui run build
pause
exit /b 1

:model_failed
echo.
echo The hand-tracking model could not be downloaded. Check your connection,
echo then run this file again. AirControl will also retry on first launch.
pause
exit /b 1

:failed
echo.
echo Setup did not finish. Check the message above, then try again.
pause
exit /b 1
