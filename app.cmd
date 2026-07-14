@echo off
rem Launch the live native AirControl app; it creates Airy in the same WebView session.
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" goto :setup
".venv\Scripts\python.exe" -c "import importlib.util as u,sys; sys.exit(0 if all(u.find_spec(n) for n in ('aircontrol','cv2','mediapipe','websockets','webview')) else 1)" >nul 2>&1
if errorlevel 1 goto :setup
goto :run

:setup
call setup.cmd
if errorlevel 1 exit /b 1

:run
if not exist "ui\dist\" goto :ui_missing

".venv\Scripts\python.exe" -m aircontrol --app
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
