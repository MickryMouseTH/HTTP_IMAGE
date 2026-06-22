@echo off
REM ============================================================================
REM  HTTP-Image-Server - Windows launcher (single-process)
REM  - บน Windows รันแบบ process เดียวเสมอ (แชร์ listening socket ข้าม process
REM    ไม่ได้ -> WinError 87). ต้องการ multi-core: รันหลาย instance คนละ port
REM    แล้ววาง reverse proxy (IIS/nginx) ไว้หน้า
REM  - ครั้งแรกจะสร้าง virtual env (.venv) + ติดตั้ง requirements ให้อัตโนมัติ
REM ============================================================================
setlocal

REM ย้ายไปยังโฟลเดอร์ที่ไฟล์ .bat อยู่ (รันจากที่ไหนก็ได้)
cd /d "%~dp0"

REM ----- หา Python -----
where py >nul 2>nul
if %errorlevel%==0 (
    set "PY=py -3"
) else (
    where python >nul 2>nul
    if %errorlevel%==0 (
        set "PY=python"
    ) else (
        echo [ERROR] ไม่พบ Python ในเครื่อง - ติดตั้งจาก https://www.python.org/downloads/ ^(ติ๊ก "Add to PATH"^)
        pause
        exit /b 1
    )
)

REM ----- สร้าง virtual env ครั้งแรก -----
if not exist ".venv\Scripts\python.exe" (
    echo [SETUP] สร้าง virtual environment ที่ .venv ...
    %PY% -m venv .venv
    if errorlevel 1 (
        echo [ERROR] สร้าง venv ไม่สำเร็จ
        pause
        exit /b 1
    )
    echo [SETUP] ติดตั้ง dependencies จาก requirements.txt ...
    ".venv\Scripts\python.exe" -m pip install --upgrade pip
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [ERROR] ติดตั้ง dependencies ไม่สำเร็จ
        pause
        exit /b 1
    )
)

REM ----- รันเซิร์ฟเวอร์ -----
echo [RUN] เริ่ม HTTP-Image-Server (single-process) ...
".venv\Scripts\python.exe" HTTP_Image_Server.py

REM ถ้าเซิร์ฟเวอร์หยุด/ครैश ให้ค้างหน้าต่างไว้ดู error
echo.
echo [STOP] เซิร์ฟเวอร์หยุดทำงานแล้ว (exit code %errorlevel%)
pause
endlocal
