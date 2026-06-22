@echo off
REM ============================================================================
REM  HTTP-Image-Server - ติดตั้งเป็น Windows Service (ผ่าน NSSM)
REM  - รันอัตโนมัติตอนเปิดเครื่อง + รีสตาร์ทเองถ้าครैช
REM  - ต้องรันแบบ "Run as administrator"
REM
REM  เตรียมก่อนรัน:
REM    1) ติดตั้ง dependencies ให้เสร็จก่อน (ดับเบิลคลิก start.bat 1 ครั้ง
REM       เพื่อให้สร้าง .venv + ติดตั้ง requirements) แล้วปิดด้วย stop.bat
REM    2) มี nssm.exe — วางไว้โฟลเดอร์เดียวกับ .bat นี้ หรืออยู่ใน PATH
REM       ดาวน์โหลด: https://nssm.cc/download
REM ============================================================================
setlocal

cd /d "%~dp0"

set "SVC_NAME=HTTP-Image-Server"

REM ----- ตรวจสิทธิ์ administrator -----
net session >nul 2>nul
if errorlevel 1 (
    echo [ERROR] ต้องรันแบบ Run as administrator
    echo         คลิกขวาที่ install-service.bat -^> Run as administrator
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
    echo         ดาวน์โหลด: https://nssm.cc/download
    pause
    exit /b 1
)

REM ----- ตรวจ venv (ต้องรัน start.bat ก่อนเพื่อสร้าง) -----
set "PYEXE=%~dp0.venv\Scripts\python.exe"
if not exist "%PYEXE%" (
    echo [ERROR] ไม่พบ .venv\Scripts\python.exe
    echo         รัน start.bat 1 ครั้งก่อน เพื่อสร้าง venv + ติดตั้ง dependencies
    pause
    exit /b 1
)

REM ----- ถ้า service มีอยู่แล้ว ลบของเก่าทิ้งก่อน -----
sc query "%SVC_NAME%" >nul 2>nul
if %errorlevel%==0 (
    echo [INFO] พบ service เดิม - หยุดและลบก่อนติดตั้งใหม่ ...
    "%NSSM%" stop "%SVC_NAME%" >nul 2>nul
    "%NSSM%" remove "%SVC_NAME%" confirm >nul 2>nul
)

echo [INSTALL] กำลังติดตั้ง service "%SVC_NAME%" ...
"%NSSM%" install "%SVC_NAME%" "%PYEXE%" "HTTP_Image_Server.py"
"%NSSM%" set "%SVC_NAME%" AppDirectory "%~dp0"
"%NSSM%" set "%SVC_NAME%" DisplayName "HTTP Image Server"
"%NSSM%" set "%SVC_NAME%" Description "HTTP-Image-Server (single-process, FastAPI/uvicorn)"
"%NSSM%" set "%SVC_NAME%" Start SERVICE_AUTO_START

REM เก็บ stdout/stderr ลงไฟล์ (เผื่อ debug นอกเหนือจาก log ของแอป)
"%NSSM%" set "%SVC_NAME%" AppStdout "%~dp0logs\service-stdout.log"
"%NSSM%" set "%SVC_NAME%" AppStderr "%~dp0logs\service-stderr.log"
REM หมุนไฟล์ stdout/stderr เมื่อใหญ่เกิน ~10MB
"%NSSM%" set "%SVC_NAME%" AppRotateFiles 1
"%NSSM%" set "%SVC_NAME%" AppRotateBytes 10485760

REM ครैชแล้วให้ NSSM รีสตาร์ทเอง (ดีเลย์ 3 วินาที)
"%NSSM%" set "%SVC_NAME%" AppExit Default Restart
"%NSSM%" set "%SVC_NAME%" AppRestartDelay 3000

echo [START] กำลังสตาร์ท service ...
"%NSSM%" start "%SVC_NAME%"
if errorlevel 1 (
    echo [WARN] สตาร์ทไม่สำเร็จ - ตรวจ logs\service-stderr.log
) else (
    echo [OK] ติดตั้ง + สตาร์ท service "%SVC_NAME%" เรียบร้อย ^(auto-start ตอนเปิดเครื่อง^)
)

echo.
pause
endlocal
