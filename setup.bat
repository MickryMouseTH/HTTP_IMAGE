@echo off
REM ============================================================================
REM  HTTP-Image-Server - venv bootstrap (shared by the other .bat files)
REM  - Creates .venv + installs requirements. No-op if .venv already exists.
REM  - Safe to `call` from other scripts: returns errorlevel 0 on success,
REM    non-zero on failure (it does NOT pause, so it won't block callers).
REM  - Finds Python on PATH (py / python / python3) OR in the default
REM    Miniforge/Miniconda/Anaconda install locations (conda usually doesn't
REM    put python on PATH).
REM ============================================================================
setlocal

cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
    REM already set up - nothing to do
    endlocal & exit /b 0
)

REM ----- 1) Try commands on PATH (verify --version actually runs) -----
set "PY="
for %%C in ("py -3" "python" "python3") do (
    if not defined PY (
        %%~C --version >nul 2>nul && set "PY=%%~C"
    )
)

REM ----- 2) Fall back to Miniforge/Miniconda/Anaconda default locations -----
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
    echo         and run the .bat from there, or install Miniforge in the default
    echo         location ^(%%USERPROFILE%%\miniforge3^), or install Python from
    echo         https://www.python.org/downloads/ ^(check "Add to PATH"^).
    endlocal & exit /b 1
)
echo [SETUP] Using Python: %PY%

echo [SETUP] Creating virtual environment at .venv ...
%PY% -m venv .venv
if errorlevel 1 (
    echo [ERROR] Failed to create venv
    endlocal & exit /b 1
)

echo [SETUP] Installing dependencies from requirements.txt ...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo [ERROR] Failed to install dependencies
    endlocal & exit /b 1
)

echo [SETUP] Done. Virtual env ready at .venv
endlocal & exit /b 0
