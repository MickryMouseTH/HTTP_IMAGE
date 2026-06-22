@echo off
REM ============================================================================
REM  HTTP-Image-Server - build the multi-worker Windows .exe (PyInstaller)
REM  Output: dist\HTTP-Image-Server\HTTP-Image-Server.exe
REM    - one .exe that spawns N worker processes (ports 50001..) and writes a
REM      SINGLE log file via a queue + one writer thread (worker-tagged).
REM    - put nginx (nginx.windows.conf, port 50000) in front.
REM
REM  Uses --onedir (NOT --onefile): far more reliable with multiprocessing
REM  spawn (onefile re-extracts the whole bundle for every worker).
REM ============================================================================
setlocal

cd /d "%~dp0"

REM Ensure venv exists (setup.bat creates it on first run)
call "%~dp0setup.bat"
if errorlevel 1 (
    pause
    exit /b 1
)

set "PYEXE=%~dp0.venv\Scripts\python.exe"

echo [BUILD] Installing PyInstaller ...
"%PYEXE%" -m pip install pyinstaller

echo [BUILD] Building HTTP-Image-Server.exe (onedir, multi-worker) ...
"%PYEXE%" -m PyInstaller --noconfirm --clean --onedir ^
    --name HTTP-Image-Server ^
    --collect-submodules uvicorn ^
    --hidden-import HTTP_Image_Server ^
    --hidden-import LogLibrary ^
    server_launcher_win.py
if errorlevel 1 (
    echo [ERROR] Build failed
    pause
    exit /b 1
)

REM Ship the config next to the .exe (the app reads/writes config + logs there)
echo [BUILD] Copying config next to the .exe ...
copy /y "HTTP-Image-Server_config.json" "dist\HTTP-Image-Server\" >nul

echo.
echo [OK] Built: dist\HTTP-Image-Server\HTTP-Image-Server.exe
echo      Run it: spawns N workers on 50001+ with ONE queued log file.
echo      Edit HTTP-Image-Server_config.json next to the .exe:
echo        "Workers": 8            (number of worker processes; 0 = auto = CPU count)
echo        "Worker_Base_Port": 50001
echo      Then put nginx in front (nginx.windows.conf, port 50000).
echo.
pause
endlocal
