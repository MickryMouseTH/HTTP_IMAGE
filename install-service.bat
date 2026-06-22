@echo off
REM ============================================================================
REM  HTTP-Image-Server - Install as a Windows Service (via NSSM)
REM  - Auto-start on boot + auto-restart on crash
REM  - Must be run as administrator
REM
REM  Before running:
REM    1) Install dependencies first (double-click start.bat once to create
REM       .venv + install requirements), then stop it with stop.bat
REM    2) Provide nssm.exe - place it next to this .bat or have it on PATH
REM       Download: https://nssm.cc/download
REM ============================================================================
setlocal

cd /d "%~dp0"

set "SVC_NAME=HTTP-Image-Server"

REM ----- Check administrator privileges -----
net session >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Must be run as administrator
    echo         Right-click install-service.bat -^> Run as administrator
    pause
    exit /b 1
)

REM ----- Locate nssm.exe -----
set "NSSM="
if exist "%~dp0nssm.exe" (
    set "NSSM=%~dp0nssm.exe"
) else (
    where nssm >nul 2>nul
    if %errorlevel%==0 set "NSSM=nssm"
)
if not defined NSSM (
    echo [ERROR] nssm.exe not found - place it in this folder or add it to PATH
    echo         Download: https://nssm.cc/download
    pause
    exit /b 1
)

REM ----- Check venv (run start.bat first to create it) -----
set "PYEXE=%~dp0.venv\Scripts\python.exe"
if not exist "%PYEXE%" (
    echo [ERROR] .venv\Scripts\python.exe not found
    echo         Run start.bat once first to create the venv + install dependencies
    pause
    exit /b 1
)

REM ----- If the service already exists, remove the old one first -----
sc query "%SVC_NAME%" >nul 2>nul
if %errorlevel%==0 (
    echo [INFO] Existing service found - stopping and removing before reinstall ...
    "%NSSM%" stop "%SVC_NAME%" >nul 2>nul
    "%NSSM%" remove "%SVC_NAME%" confirm >nul 2>nul
)

echo [INSTALL] Installing service "%SVC_NAME%" ...
"%NSSM%" install "%SVC_NAME%" "%PYEXE%" "HTTP_Image_Server.py"
"%NSSM%" set "%SVC_NAME%" AppDirectory "%~dp0"
"%NSSM%" set "%SVC_NAME%" DisplayName "HTTP Image Server"
"%NSSM%" set "%SVC_NAME%" Description "HTTP-Image-Server (single-process, FastAPI/uvicorn)"
"%NSSM%" set "%SVC_NAME%" Start SERVICE_AUTO_START

REM Capture stdout/stderr to files (for debugging on top of the app log)
"%NSSM%" set "%SVC_NAME%" AppStdout "%~dp0logs\service-stdout.log"
"%NSSM%" set "%SVC_NAME%" AppStderr "%~dp0logs\service-stderr.log"
REM Rotate stdout/stderr files when larger than ~10MB
"%NSSM%" set "%SVC_NAME%" AppRotateFiles 1
"%NSSM%" set "%SVC_NAME%" AppRotateBytes 10485760

REM On crash, let NSSM restart it (3 second delay)
"%NSSM%" set "%SVC_NAME%" AppExit Default Restart
"%NSSM%" set "%SVC_NAME%" AppRestartDelay 3000

echo [START] Starting service ...
"%NSSM%" start "%SVC_NAME%"
if errorlevel 1 (
    echo [WARN] Failed to start - check logs\service-stderr.log
) else (
    echo [OK] Service "%SVC_NAME%" installed and started ^(auto-start on boot^)
)

echo.
pause
endlocal
