@echo off
REM ============================================================================
REM  HTTP-Image-Server - ถอน Windows Service (ผ่าน NSSM)
REM  - ต้องรันแบบ "Run as administrator"
REM ============================================================================
setlocal

cd /d "%~dp0"

set "SVC_NAME=HTTP-Image-Server"

REM ----- ตรวจสิทธิ์ administrator -----
net session >nul 2>nul
if errorlevel 1 (
    echo [ERROR] ต้องรันแบบ Run as administrator
    echo         คลิกขวาที่ uninstall-service.bat -^> Run as administrator
    pause
    exit /b 1
)

REM ----- หา nssm.exe -----
set "NSSM="
if exist "%~dp0nssm.exe" (
    set "NSSM=%~dp0nssm.exe"
) else (
    where nssm >nul 2>nul
    if %errorlevel%==0 set "NSSM=nssm"
)
if not defined NSSM (
    echo [ERROR] ไม่พบ nssm.exe - วางไว้โฟลเดอร์นี้ หรือเพิ่มลง PATH
    pause
    exit /b 1
)

sc query "%SVC_NAME%" >nul 2>nul
if not %errorlevel%==0 (
    echo [INFO] ไม่พบ service "%SVC_NAME%" ^(อาจถอนไปแล้ว^)
    pause
    exit /b 0
)

echo [STOP] หยุด service "%SVC_NAME%" ...
"%NSSM%" stop "%SVC_NAME%" >nul 2>nul

echo [REMOVE] ถอน service "%SVC_NAME%" ...
"%NSSM%" remove "%SVC_NAME%" confirm
if errorlevel 1 (
    echo [WARN] ถอน service ไม่สำเร็จ
) else (
    echo [OK] ถอน service "%SVC_NAME%" เรียบร้อย
)

echo.
pause
endlocal
