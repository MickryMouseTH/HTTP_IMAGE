@echo off
REM ============================================================================
REM  HTTP-Image-Server - Windows stop script
REM  - หา process ที่ฟัง (LISTENING) อยู่บน Port_Server แล้วสั่งปิด
REM  - อ่าน port จาก HTTP-Image-Server_config.json อัตโนมัติ (ไม่เจอ -> ใช้ 8080)
REM ============================================================================
setlocal enabledelayedexpansion

cd /d "%~dp0"

REM ----- อ่าน Port_Server จากไฟล์ config -----
set "PORT="
for /f "tokens=2 delims=:," %%A in ('findstr /i "\"Port_Server\"" "HTTP-Image-Server_config.json" 2^>nul') do (
    for /f "tokens=* delims= " %%B in ("%%A") do set "PORT=%%B"
)
if not defined PORT set "PORT=8080"
REM ตัดช่องว่างที่อาจติดมา
set "PORT=%PORT: =%"

echo [STOP] กำลังหา process ที่ฟังอยู่บน port %PORT% ...

set "FOUND="
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /r /c:":%PORT% .*LISTENING"') do (
    if not "%%P"=="0" (
        set "FOUND=1"
        echo [STOP] พบ PID %%P -> กำลังปิด ...
        taskkill /PID %%P /F >nul 2>nul
        if errorlevel 1 (
            echo [WARN] ปิด PID %%P ไม่สำเร็จ ^(อาจต้องรัน .bat แบบ Run as administrator^)
        ) else (
            echo [OK] ปิด PID %%P เรียบร้อย
        )
    )
)

if not defined FOUND (
    echo [INFO] ไม่พบเซิร์ฟเวอร์ที่ฟังอยู่บน port %PORT% ^(อาจหยุดไปแล้ว^)
)

echo.
pause
endlocal
