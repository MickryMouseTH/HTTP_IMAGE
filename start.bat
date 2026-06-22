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

REM ----- Locate a WORKING Python -----
REM Try each candidate by actually running it (a present `py` launcher with no
REM installed Python prints "No installed Python found!", so a where/exists
REM check is not enough - we verify --version succeeds).
set "PY="
for %%C in ("py -3" "python" "python3") do (
    if not defined PY (
        %%~C --version >nul 2>nul && set "PY=%%~C"
    )
)
if not defined PY (
    echo [ERROR] No working Python found.
    echo         The `py` launcher may exist but no Python is installed.
    echo         Install Python from https://www.python.org/downloads/ ^(check "Add to PATH"^),
    echo         then run start.bat again.
    pause
    exit /b 1
)
echo [SETUP] Using Python: %PY%

REM ----- Create virtual env on first run -----
if not exist ".venv\Scripts\python.exe" (
    echo [SETUP] Creating virtual environment at .venv ...
    %PY% -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Failed to create venv
        pause
        exit /b 1
    )
    echo [SETUP] Installing dependencies from requirements.txt ...
    ".venv\Scripts\python.exe" -m pip install --upgrade pip
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [ERROR] Failed to install dependencies
        pause
        exit /b 1
    )
)

REM ----- Run the server -----
echo [RUN] Starting HTTP-Image-Server (single-process) ...
".venv\Scripts\python.exe" HTTP_Image_Server.py

REM If the server stops/crashes, keep the window open to show the error
echo.
echo [STOP] Server stopped (exit code %errorlevel%)
pause
endlocal
