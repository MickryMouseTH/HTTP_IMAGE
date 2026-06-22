@echo off
REM ============================================================================
REM  HTTP-Image-Server - Windows multi-instance cluster launcher
REM  - Starts COUNT single-process instances on consecutive ports
REM    (BASE_PORT, BASE_PORT+1, ...). Put nginx in front to load-balance
REM    (see nginx.windows.conf) so you actually use multiple CPU cores.
REM  - Each instance gets its own log file (HTTP_IMAGE_PORT in the file name)
REM    so the instances never fight over rotating the same log (WinError 32).
REM
REM  Prerequisite: run start.bat ONCE first to create .venv + install deps.
REM ============================================================================
setlocal

cd /d "%~dp0"

REM ----- Tunables -----
set "BASE_PORT=50001"
set "COUNT=8"

REM Ensure venv exists (setup.bat creates it on first run)
call "%~dp0setup.bat"
if errorlevel 1 (
    pause
    exit /b 1
)
set "PYEXE=%~dp0.venv\Scripts\python.exe"

echo [CLUSTER] Starting %COUNT% instances from port %BASE_PORT% ...
set /a "LAST=COUNT-1"
for /l %%i in (0,1,%LAST%) do call :launch %%i

echo.
echo [CLUSTER] Done. Each instance runs in its own minimized window.
echo           Put nginx in front (nginx.windows.conf) and hit http://localhost/
echo           Stop everything with stop-cluster.bat
echo.
pause
endlocal
goto :eof

:launch
REM %1 = offset; compute this instance's port and launch it with HTTP_IMAGE_PORT set
set /a "P=BASE_PORT+%1"
set "HTTP_IMAGE_PORT=%P%"
echo [START] instance on port %P% ...
start "HTTP-Image :%P%" /min "%PYEXE%" HTTP_Image_Server.py
goto :eof
