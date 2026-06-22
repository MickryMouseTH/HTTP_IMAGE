@echo off
REM ============================================================================
REM  HTTP-Image-Server - Windows launcher (single-process)
REM  - On Windows always run single-process (a listening socket cannot be
REM    shared across processes -> WinError 87). For multi-core: run several
REM    instances on different ports behind a reverse proxy (IIS/nginx).
REM  - First run auto-creates a virtual env (.venv) + installs requirements.
REM ============================================================================
setlocal

REM Move to the folder where this .bat lives (run from anywhere)
cd /d "%~dp0"

REM ----- Ensure venv exists (setup.bat creates it on first run) -----
call "%~dp0setup.bat"
if errorlevel 1 (
    pause
    exit /b 1
)

REM ----- Run the server -----
echo [RUN] Starting HTTP-Image-Server (single-process) ...
".venv\Scripts\python.exe" HTTP_Image_Server.py

REM If the server stops/crashes, keep the window open to show the error
echo.
echo [STOP] Server stopped (exit code %errorlevel%)
pause
endlocal
