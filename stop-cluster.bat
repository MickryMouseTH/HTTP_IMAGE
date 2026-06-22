@echo off
REM ============================================================================
REM  HTTP-Image-Server - stop the Windows multi-instance cluster
REM  - Kills whatever is LISTENING on each cluster port (BASE_PORT .. +COUNT-1)
REM  - Must match the BASE_PORT/COUNT used in start-cluster.bat
REM ============================================================================
setlocal

cd /d "%~dp0"

set "BASE_PORT=8080"
set "COUNT=8"

echo [CLUSTER] Stopping %COUNT% instances from port %BASE_PORT% ...
set /a "LAST=COUNT-1"
for /l %%i in (0,1,%LAST%) do call :killport %%i

echo.
echo [CLUSTER] Done.
echo.
pause
endlocal
goto :eof

:killport
set /a "P=BASE_PORT+%1"
set "FOUND="
for /f "tokens=5" %%a in ('netstat -ano ^| findstr /r /c:":%P% .*LISTENING"') do (
    if not "%%a"=="0" (
        set "FOUND=1"
        taskkill /PID %%a /F >nul 2>nul
        if errorlevel 1 (
            echo [WARN] port %P% - failed to kill PID %%a ^(try Run as administrator^)
        ) else (
            echo [OK] port %P% - killed PID %%a
        )
    )
)
if not defined FOUND echo [INFO] port %P% - nothing listening
goto :eof
