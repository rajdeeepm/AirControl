@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>&1
if errorlevel 1 goto :python_missing
py -3 -c "import platform,sys; ok=sys.version_info >= (3,11) and sys.maxsize > 2**32 and platform.machine().lower() in ('amd64','x86_64'); sys.exit(0 if ok else 1)" >nul 2>&1
if errorlevel 1 goto :python_missing

if not exist ".venv\Scripts\python.exe" (
  echo Creating AirControl's private Python environment...
  py -3 -m venv .venv
  if errorlevel 1 goto :failed
)

echo Installing AirControl...
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto :failed
".venv\Scripts\python.exe" -m pip install -e ".[dev]"
if errorlevel 1 goto :failed

echo.
echo AirControl is ready.
exit /b 0

:python_missing
echo.
echo AirControl needs x64 Python 3.11 or newer from python.org.
echo Install it with the Python Launcher option, then run this file again.
echo https://www.python.org/downloads/windows/
pause
exit /b 1

:failed
echo.
echo Setup did not finish. Check the message above, then try again.
pause
exit /b 1
