@echo off
REM ============================================================================
REM  HTTP-Image-Server - Windows stop script
REM  - Find the process LISTENING on Port_Server and kill it
REM  - Reads the port from HTTP-Image-Server_config.json (falls back to 8080)
REM ============================================================================
setlocal enabledelayedexpansion

cd /d "%~dp0"

REM ----- Read Port_Server from the config file -----
set "PORT="
for /f "tokens=2 delims=:," %%A in ('findstr /i "\"Port_Server\"" "HTTP-Image-Server_config.json" 2^>nul') do (
    for /f "tokens=* delims= " %%B in ("%%A") do set "PORT=%%B"
)
if not defined PORT set "PORT=8080"
REM Strip any stray spaces
set "PORT=%PORT: =%"

echo [STOP] Looking for a process listening on port %PORT% ...

set "FOUND="
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /r /c:":%PORT% .*LISTENING"') do (
    if not "%%P"=="0" (
        set "FOUND=1"
        echo [STOP] Found PID %%P -> terminating ...
        taskkill /PID %%P /F >nul 2>nul
        if errorlevel 1 (
            echo [WARN] Failed to kill PID %%P ^(try running this .bat as administrator^)
        ) else (
            echo [OK] PID %%P terminated
        )
    )
)

if not defined FOUND (
    echo [INFO] No server found listening on port %PORT% ^(may already be stopped^)
)

echo.
pause
endlocal
