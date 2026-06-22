@echo off
REM ============================================================================
REM  HTTP-Image-Server - Uninstall the Windows Service (via NSSM)
REM  - Must be run as administrator
REM ============================================================================
setlocal

cd /d "%~dp0"

set "SVC_NAME=HTTP-Image-Server"

REM ----- Check administrator privileges -----
net session >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Must be run as administrator
    echo         Right-click uninstall-service.bat -^> Run as administrator
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
    pause
    exit /b 1
)

sc query "%SVC_NAME%" >nul 2>nul
if not %errorlevel%==0 (
    echo [INFO] Service "%SVC_NAME%" not found ^(may already be removed^)
    pause
    exit /b 0
)

echo [STOP] Stopping service "%SVC_NAME%" ...
"%NSSM%" stop "%SVC_NAME%" >nul 2>nul

echo [REMOVE] Removing service "%SVC_NAME%" ...
"%NSSM%" remove "%SVC_NAME%" confirm
if errorlevel 1 (
    echo [WARN] Failed to remove service
) else (
    echo [OK] Service "%SVC_NAME%" removed
)

echo.
pause
endlocal
