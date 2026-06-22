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
REM 1) Try commands on PATH by actually running them (a present `py` launcher
REM    with no installed Python prints "No installed Python found!", so a
REM    where/exists check is not enough - we verify --version succeeds).
set "PY="
for %%C in ("py -3" "python" "python3") do (
    if not defined PY (
        %%~C --version >nul 2>nul && set "PY=%%~C"
    )
)

REM 2) Miniforge/Miniconda/Anaconda usually do NOT put python on PATH (you
REM    normally `conda activate` first). Look in the default install locations.
if not defined PY (
    for %%P in (
        "%USERPROFILE%\miniforge3\python.exe"
        "%LOCALAPPDATA%\miniforge3\python.exe"
        "%PROGRAMDATA%\miniforge3\python.exe"
        "C:\miniforge3\python.exe"
        "%USERPROFILE%\miniconda3\python.exe"
        "%USERPROFILE%\Anaconda3\python.exe"
        "%PROGRAMDATA%\Anaconda3\python.exe"
    ) do (
        if not defined PY if exist "%%~P" set PY="%%~P"
    )
)

if not defined PY (
    echo [ERROR] No working Python found.
    echo         Checked PATH ^(py / python / python3^) and common Miniforge/conda paths.
    echo         If you use Miniforge, open the "Miniforge Prompt" ^(conda activate base^)
    echo         and run start.bat from there, or install it in the default location
    echo         ^(%%USERPROFILE%%\miniforge3^), or install Python from
    echo         https://www.python.org/downloads/ ^(check "Add to PATH"^),
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
