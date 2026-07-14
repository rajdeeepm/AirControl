@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" goto :setup
".venv\Scripts\python.exe" -c "import importlib.util as u,sys; sys.exit(0 if all(u.find_spec(n) for n in ('aircontrol','cv2','mediapipe','websockets')) else 1)" >nul 2>&1
if errorlevel 1 goto :setup
goto :run

:setup
call setup.cmd
if errorlevel 1 exit /b 1

:run
".venv\Scripts\python.exe" -m aircontrol --practice
set "AIRCONTROL_EXIT=%ERRORLEVEL%"
if not "%AIRCONTROL_EXIT%"=="0" pause
exit /b %AIRCONTROL_EXIT%
