@echo off
REM ============================================================================
REM  HTTP-Image-Server - Install the WHOLE Windows cluster as services (NSSM)
REM  One command installs everything, all auto-start on boot + auto-restart:
REM    - COUNT app instances (HTTP-Image-Server-8080 .. 8087)
REM    - nginx reverse proxy in front (HTTP-Image-nginx)
REM  Must be run as administrator.
REM
REM  Prerequisites:
REM    1) nssm.exe next to this .bat or on PATH            (https://nssm.cc/download)
REM    2) nginx for Windows extracted to %NGINX_DIR% below (http://nginx.org/en/download.html)
REM  (.venv is created automatically via setup.bat on first run.)
REM ============================================================================
setlocal

cd /d "%~dp0"

REM ----- Tunables -----
set "SVC_PREFIX=HTTP-Image-Server"
set "NGSVC=HTTP-Image-nginx"
set "BASE_PORT=50001"
set "COUNT=8"
set "NGINX_DIR=C:\nginx"

REM ----- Admin check -----
net session >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Must be run as administrator
    echo         Right-click install-cluster-service.bat -^> Run as administrator
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

REM ----- Ensure venv exists (setup.bat creates it on first run) -----
call "%~dp0setup.bat"
if errorlevel 1 (
    pause
    exit /b 1
)
set "PYEXE=%~dp0.venv\Scripts\python.exe"

REM ----- Install the COUNT app instances -----
echo [CLUSTER] Installing %COUNT% app services from port %BASE_PORT% ...
set /a "LAST=COUNT-1"
for /l %%i in (0,1,%LAST%) do call :install_app %%i

REM ----- Install nginx as a service (optional, skipped if not found) -----
call :install_nginx

echo.
echo [DONE] Cluster installed. Services auto-start on boot.
echo        Manage: sc query state^= all ^| findstr HTTP-Image
echo        Remove: uninstall-cluster-service.bat  (Run as administrator)
echo        Browse: http://localhost:50000/image/...
echo.
pause
endlocal
goto :eof

REM ---------------------------------------------------------------------------
:install_app
set /a "P=BASE_PORT+%1"
set "SVC=%SVC_PREFIX%-%P%"

REM remove old one first (idempotent reinstall)
sc query "%SVC%" >nul 2>nul
if %errorlevel%==0 (
    "%NSSM%" stop "%SVC%" >nul 2>nul
    "%NSSM%" remove "%SVC%" confirm >nul 2>nul
)

echo [INSTALL] %SVC%  (HTTP_IMAGE_PORT=%P%)
"%NSSM%" install "%SVC%" "%PYEXE%" "HTTP_Image_Server.py"
"%NSSM%" set "%SVC%" AppDirectory "%~dp0"
"%NSSM%" set "%SVC%" AppEnvironmentExtra HTTP_IMAGE_PORT=%P%
"%NSSM%" set "%SVC%" DisplayName "HTTP Image Server :%P%"
"%NSSM%" set "%SVC%" Description "HTTP-Image-Server instance on port %P%"
"%NSSM%" set "%SVC%" Start SERVICE_AUTO_START
"%NSSM%" set "%SVC%" AppStdout "%~dp0logs\service-%P%-stdout.log"
"%NSSM%" set "%SVC%" AppStderr "%~dp0logs\service-%P%-stderr.log"
"%NSSM%" set "%SVC%" AppRotateFiles 1
"%NSSM%" set "%SVC%" AppRotateBytes 10485760
"%NSSM%" set "%SVC%" AppExit Default Restart
"%NSSM%" set "%SVC%" AppRestartDelay 3000
"%NSSM%" start "%SVC%" >nul 2>nul
goto :eof

REM ---------------------------------------------------------------------------
:install_nginx
if not exist "%NGINX_DIR%\nginx.exe" (
    echo [WARN] nginx not found at %NGINX_DIR%\nginx.exe - skipping nginx service.
    echo        Install nginx for Windows there ^(or edit NGINX_DIR^) and re-run.
    goto :eof
)

REM deploy our config (back up any existing one)
set "NCONF=%NGINX_DIR%\conf\nginx.conf"
if exist "%NCONF%" copy /y "%NCONF%" "%NCONF%.bak" >nul
copy /y "%~dp0nginx.windows.conf" "%NCONF%" >nul
echo [INSTALL] copied nginx.windows.conf -^> %NCONF%  (old backed up to nginx.conf.bak)

sc query "%NGSVC%" >nul 2>nul
if %errorlevel%==0 (
    "%NSSM%" stop "%NGSVC%" >nul 2>nul
    "%NSSM%" remove "%NGSVC%" confirm >nul 2>nul
)

echo [INSTALL] %NGSVC%  (reverse proxy on port 50000)
"%NSSM%" install "%NGSVC%" "%NGINX_DIR%\nginx.exe"
"%NSSM%" set "%NGSVC%" AppDirectory "%NGINX_DIR%"
"%NSSM%" set "%NGSVC%" DisplayName "HTTP Image Server - nginx"
"%NSSM%" set "%NGSVC%" Description "nginx reverse proxy for the HTTP-Image-Server cluster"
"%NSSM%" set "%NGSVC%" Start SERVICE_AUTO_START
"%NSSM%" set "%NGSVC%" AppStdout "%~dp0logs\service-nginx-stdout.log"
"%NSSM%" set "%NGSVC%" AppStderr "%~dp0logs\service-nginx-stderr.log"
"%NSSM%" set "%NGSVC%" AppExit Default Restart
"%NSSM%" set "%NGSVC%" AppRestartDelay 3000
REM start nginx last, after the app instances are up
"%NSSM%" start "%NGSVC%" >nul 2>nul
goto :eof
