@echo off
REM ============================================================================
REM  HTTP-Image-Server - Uninstall the whole cluster (app instances + nginx)
REM  Must be run as administrator. Must match BASE_PORT/COUNT/names used in
REM  install-cluster-service.bat.
REM ============================================================================
setlocal

cd /d "%~dp0"

set "SVC_PREFIX=HTTP-Image-Server"
set "NGSVC=HTTP-Image-nginx"
set "BASE_PORT=50001"
set "COUNT=8"

REM ----- Admin check -----
net session >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Must be run as administrator
    echo         Right-click uninstall-cluster-service.bat -^> Run as administrator
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

REM ----- Remove nginx service first -----
call :remove "%NGSVC%"

REM ----- Remove app instances -----
set /a "LAST=COUNT-1"
for /l %%i in (0,1,%LAST%) do call :remove_app %%i

echo.
echo [DONE] Cluster services removed.
echo.
pause
endlocal
goto :eof

REM ---------------------------------------------------------------------------
:remove_app
set /a "P=BASE_PORT+%1"
call :remove "%SVC_PREFIX%-%P%"
goto :eof

:remove
set "SVC=%~1"
sc query "%SVC%" >nul 2>nul
if not %errorlevel%==0 (
    echo [INFO] %SVC% - not installed
    goto :eof
)
echo [REMOVE] %SVC%
"%NSSM%" stop "%SVC%" >nul 2>nul
"%NSSM%" remove "%SVC%" confirm >nul 2>nul
goto :eof
