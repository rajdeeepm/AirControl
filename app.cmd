@echo off
rem Safe default: app.cmd starts AirControl in practice mode so the UI cannot inject real input.
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" goto :setup
".venv\Scripts\python.exe" -c "import importlib.util as u,sys; sys.exit(0 if all(u.find_spec(n) for n in ('aircontrol','cv2','mediapipe')) else 1)" >nul 2>&1
if errorlevel 1 goto :setup
goto :run

:setup
call setup.cmd
if errorlevel 1 exit /b 1

:run
if not exist "ui\dist\" goto :ui_missing

start "AirControl daemon" /min ".venv\Scripts\python.exe" -m aircontrol --serve --practice
if errorlevel 1 goto :daemon_failed

".venv\Scripts\python.exe" "scripts\serve_ui.py"
set "AIRCONTROL_EXIT=%ERRORLEVEL%"
if not "%AIRCONTROL_EXIT%"=="0" pause
exit /b %AIRCONTROL_EXIT%

:ui_missing
echo.
echo The AirControl app UI has not been built yet.
echo Run this command from the AirControl folder, then try app.cmd again:
echo npm --prefix ui install ^&^& npm --prefix ui run build
pause
exit /b 2

:daemon_failed
echo.
echo The AirControl daemon could not start.
pause
exit /b 1
