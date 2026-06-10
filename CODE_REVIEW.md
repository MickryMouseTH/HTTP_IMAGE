# รายงานตรวจสอบโค้ด — HTTP-Image-Server

วันที่ตรวจสอบ: 2026-06-10
ไฟล์ที่ตรวจ: `HTTP-Image-Server.py`, `LogLibrary.py`, `HTTP-Image-Server_config.json`, `Server.spec`
เวอร์ชันโปรแกรม: 1.7

---

## สรุปผู้บริหาร (TL;DR)

| # | ปัญหา | ความรุนแรง | ไฟล์ |
|---|-------|-----------|------|
| 1 | **หมุน/สร้างไฟล์ log ไม่ได้เมื่อถึง Limit (10 MB) ตอนรันหลาย process บน Windows** | 🔴 สูง (ตรงกับอาการที่เจอ) | `LogLibrary.py` |
| 2 | `Server.spec` ชี้ entry script ผิดชื่อ (`Server.py` แต่ไฟล์จริงคือ `HTTP-Image-Server.py`) | 🔴 สูง | `Server.spec` |
| 3 | `Max_Workers` มีใน config แต่ **ไม่ถูกใช้งานเลย** | 🟠 กลาง | `HTTP-Image-Server.py` |
| 4 | `InterceptHandler` ไม่ตั้ง `depth` ทำให้ log ทุกบรรทัดของ uvicorn โชว์ฟังก์ชันเป็น `emit` | 🟡 ต่ำ | `HTTP-Image-Server.py` |
| 5 | ค่า path ใน config ถูก escape เกิน (`C:\\\\DC` → กลายเป็น `C:\\DC`) | 🟡 ต่ำ | `*_config.json` |
| 6 | ค่า default ของ Port ไม่ตรงกันระหว่าง config (8080) กับ fallback ในโค้ด (50000) | 🟡 ต่ำ | `HTTP-Image-Server.py` |
| 7 | `Load_Config` ไม่กันไฟล์ config เสีย/พังด้วย try-except | 🟡 ต่ำ | `LogLibrary.py` |
| 8 | โค้ดส่วนเกิน: `global script_dir`, `default_config = default_config` | ⚪ cosmetic | `LogLibrary.py` |

---

## 🔴 ปัญหาที่ 1 — สร้าง/หมุนไฟล์ log ไม่ได้เมื่อถึง Limit (ปัญหาหลัก)

### อาการ
เมื่อไฟล์ log โตถึง `Log_Size` ("10 MB") loguru จะพยายาม **rotate** (เปลี่ยนชื่อไฟล์เดิม → บีบอัด zip → เปิดไฟล์ใหม่) แต่กลับสร้าง/หมุนไฟล์ไม่ได้ หรือเกิด error

### หลักฐานจากไฟล์ log ที่มีอยู่จริง
```
... | INFO | 25748 | Loguru_Logging | Start HTTP-Image-Server Version 1.7
... | INFO | 12452 | Loguru_Logging | Start HTTP-Image-Server Version 1.7
... | ERROR | 31048 | emit | Error loading ASGI app. Could not import module "main".
... | INFO | 20972 | emit | Waiting for child process [35552]
... | INFO | 20972 | emit | Child process [35552] died
```
จะเห็นว่ามี **หลาย PID** (25748, 12452, 31048, 4216, 20972 ...) เขียนลง **ไฟล์ log เดียวกัน** พร้อมกัน → นี่คือสัญญาณว่าโปรแกรมเคยรัน/กำลังจะรันแบบ **multi-process (uvicorn workers)**

### สาเหตุที่แท้จริง (Root Cause)
ใน `LogLibrary.py` (บรรทัด 106-113) sink ของไฟล์ถูกตั้งค่าแบบนี้:
```python
logger.add(
    log_file,
    format="...",
    level=log_Level,
    rotation=Log_Size,         # หมุนเมื่อถึง 10 MB
    retention=f"{log_Backup} days",
    compression="zip"
    # ❌ ไม่มี enqueue=True
)
```

ปัญหาเกิดจาก **2 เงื่อนไขรวมกัน**:

1. **ทุก process เปิดไฟล์ log ตัวเดียวกัน** — เมื่อรันแบบ multi-process ทุก worker จะ `import LogLibrary` แล้วเรียก `logger.add(log_file, ...)` ไปที่ path เดียวกัน ทำให้มี **file handle หลายตัวค้างบนไฟล์เดียว**

2. **Windows ล็อกไฟล์ตอน rename/zip** — เมื่อ process A ถึง 10 MB มันจะสั่ง rename ไฟล์เดิมเพื่อหมุน แต่ process B/C/D ยัง **ถือ handle ของไฟล์นั้นค้างอยู่** → Windows คืน error:
   ```
   PermissionError: [WinError 32] The process cannot access the file
   because it is being used by another process
   ```
   หรือ `FileNotFoundError` (ไฟล์ถูก process อื่นหมุนไปแล้ว) → **rotation ล้มเหลว → log หยุดเขียน / สร้างไฟล์ใหม่ไม่ได้**

> หมายเหตุ: loguru แบบ default (`enqueue=False`) **ไม่ปลอดภัยสำหรับ multiprocessing** เอกสาร loguru ระบุชัดว่าต้องใช้ `enqueue=True` เมื่อเขียนไฟล์เดียวกันจากหลาย process

### วิธีแก้ (เลือกได้)

#### แนวทาง A — ปลอดภัยสุด: ทำให้ปลอดภัยกับ multiprocess + แยกไฟล์ตาม process
แก้ `Loguru_Logging` ใน `LogLibrary.py`:
```python
logger.add(
    log_file,
    format="{time} | {level} | {thread.id} | {function} | {message}",
    level=log_Level,
    rotation=Log_Size,
    retention=f"{log_Backup} days",
    compression="zip",
    enqueue=True,        # ✅ ส่ง log ผ่าน queue + เขียนด้วย thread เดียว = ปลอดภัยกับ multiprocess
    catch=True,          # ✅ ถ้า sink error (เช่น rotate ล้มเหลว) ไม่ทำให้โปรแกรมล่ม
)
```
- `enqueue=True` ทำให้การหมุนไฟล์เกิดในกระบวนการเดียวที่ควบคุมคิว ลดการชนกันของ handle
- `catch=True` กัน exception จาก sink ไม่ให้ propagate ออกมา

#### แนวทาง B — เด็ดขาดสุด: ให้แต่ละ process เขียนไฟล์ของตัวเอง
ใส่ PID ลงในชื่อไฟล์ เพื่อไม่ให้ process ชนกันที่ไฟล์เดียวเลย:
```python
log_file_name = f'{Program_Name}_{Program_Version}_{{time:YYYY-MM-DD}}_pid{os.getpid()}.log'
```
> เหมาะเมื่อจำเป็นต้องรันหลาย worker จริง ๆ แลกกับมีไฟล์ log หลายไฟล์

#### แนวทาง C — ง่ายสุด: บังคับรัน process เดียว (แนะนำสำหรับงานนี้)
ปัญหานี้จะ **หายไปทันที** ถ้ารัน 1 process ซึ่งโค้ดปัจจุบันใน `HTTP-Image-Server.py` ก็เรียก `uvicorn.run(app, ...)` แบบ **ไม่ส่ง `workers=`** อยู่แล้ว (= 1 process)
ดังนั้นแค่ **อย่าเปิด multi-worker** + ตรวจให้แน่ใจว่าไม่มี instance เก่าค้างถือไฟล์ log อยู่ ก็พอ
(ดูปัญหาที่ 3 ประกอบ — `Max_Workers` ที่ค้างใน config อาจทำให้เข้าใจผิดว่าระบบรันหลาย worker)

> **คำแนะนำ:** ใช้ **A + C ร่วมกัน** — เพิ่ม `enqueue=True, catch=True` และคงสถาปัตยกรรม process เดียวไว้

---

## 🔴 ปัญหาที่ 2 — `Server.spec` ชี้ไฟล์ entry ผิด

`Server.spec` บรรทัด 5:
```python
a = Analysis(['Server.py'], ...)
```
แต่ไฟล์โปรแกรมจริงชื่อ **`HTTP-Image-Server.py`** → PyInstaller จะ build ไม่ผ่าน (หาไฟล์ `Server.py` ไม่เจอ)

นอกจากนี้ในไฟล์ log ยังพบ:
```
Error loading ASGI app. Could not import module "main".
```
สื่อว่าเคยมีโค้ดเวอร์ชันที่เรียก `uvicorn.run("main:app", ...)` ด้วย string แต่ module จริงไม่ได้ชื่อ `main` → import ไม่เจอ → worker ตายทันที (`Child process died`) ปัจจุบันโค้ดแก้เป็นส่ง `app` object ตรง ๆ แล้ว แต่ **ไฟล์ `.spec` ยังตามไม่ทัน**

**วิธีแก้:** แก้ `.spec` ให้ตรงชื่อไฟล์ และตั้ง name ให้สอดคล้อง
```python
a = Analysis(['HTTP-Image-Server.py'], ...)
...
exe = EXE(..., name='HTTP-Image-Server', ...)
```
และเพิ่ม hidden imports ที่ PyInstaller มักมองไม่เห็นกับ uvicorn:
```python
hiddenimports=['uvicorn.logging', 'uvicorn.loops.auto',
               'uvicorn.protocols.http.auto', 'uvicorn.lifespan.on'],
```

---

## 🟠 ปัญหาที่ 3 — `Max_Workers` ไม่ถูกใช้งาน

`config["Max_Workers"] = 4` ถูกประกาศทั้งใน `default_config` และไฟล์ config จริง แต่ใน `uvicorn.run(...)` (บรรทัด 169-176) **ไม่มีการส่ง `workers=config["Max_Workers"]`** เลย

ผลกระทบ:
- ผู้ใช้ตั้ง `Max_Workers: 4` โดยคาดว่าจะได้ 4 worker แต่จริง ๆ ได้ **1 process** → เข้าใจผิด
- ถ้าอนาคตมีคนเผลอเพิ่ม `workers=4` กลับมา → จะชนปัญหาที่ 1 ทันที (และยังต้องเปลี่ยน `app` เป็น string `"HTTP-Image-Server:app"` เพราะ uvicorn บังคับใช้ import string เมื่อ `workers > 1`)

**วิธีแก้:** เลือกอย่างใดอย่างหนึ่ง
- ถ้าไม่ต้องการ multi-worker → **ลบ `Max_Workers` ออกจาก config** เพื่อไม่ให้สับสน
- ถ้าต้องการจริง → ต้องทำพร้อมกัน: ใช้ import-string, ตั้ง `workers=`, และแก้ logging ตามปัญหาที่ 1 (แนวทาง A หรือ B)

---

## 🟡 ปัญหาที่ 4 — log ของ uvicorn โชว์ฟังก์ชันเป็น `emit` ทุกบรรทัด

ใน log:
```
... | ERROR | 31048 | emit | Error loading ASGI app...
... | INFO  | 20972 | emit | Waiting for child process...
```
ทุกบรรทัดที่มาจาก uvicorn จะมีฟังก์ชัน = `emit` (ชื่อเมธอดของ `InterceptHandler`) แทนที่จะเป็นฟังก์ชันต้นทางจริง เพราะ `InterceptHandler.emit` (บรรทัด 45-52) **ไม่ได้ตั้ง `depth`** ให้ loguru ไล่ stack กลับไปหาผู้เรียกจริง

**วิธีแก้** (recipe มาตรฐานของ loguru):
```python
class InterceptHandler(logging.Handler):
    def emit(self, record: logging.LogRecord):
        try:
            level = loguru_logger.level(record.levelname).name
        except Exception:
            level = record.levelno

        frame, depth = logging.currentframe(), 2
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1

        loguru_logger.opt(depth=depth, exception=record.exc_info).log(
            level, record.getMessage()
        )
```

---

## 🟡 ปัญหาที่ 5 — path ใน config ถูก escape เกิน

ใน `HTTP-Image-Server_config.json`:
```json
"path": "C:\\\\DC"
```
JSON `\\\\` → ถูกแปลงเป็นสตริงจริง `C:\\DC` (มี backslash 2 ตัวติดกัน) ซึ่งไม่ใช่ path ที่ถูกต้อง โชคดีที่ `os.path.normpath` / `os.path.abspath` ใน `_safe_join` ยุบให้เหลือ `C:\DC` ได้ จึงยัง "บังเอิญใช้งานได้" แต่เป็นการพึ่งพฤติกรรมโดยไม่ตั้งใจ

**ควรเป็น:** (backslash คู่เดียวพอใน JSON)
```json
"path": "C:\\DC"
```
(ใน `default_config` ของไฟล์ `.py` ก็ควรแก้ `"C:\\\\DC"` → `"C:\\DC"` เช่นกัน)

---

## 🟡 ปัญหาที่ 6 — ค่า default ของ Port ไม่ตรงกัน

`HTTP-Image-Server.py` บรรทัด 172:
```python
port=int(config.get("Port_Server", 50000)),
```
fallback คือ **50000** แต่ค่า default จริงในทั้ง `default_config` และไฟล์ config คือ **8080** → ถ้า key `Port_Server` หาย พฤติกรรมจะเปลี่ยนเป็น 50000 แบบเงียบ ๆ ควรใช้ค่าเดียวกันให้สอดคล้อง (`8080`)

---

## 🟡 ปัญหาที่ 7 — `Load_Config` ไม่กันไฟล์ config เสีย

`LogLibrary.py` บรรทัด 65-66:
```python
with open(config_path, 'r') as config_file:
    config = json.load(config_file)
```
ถ้าไฟล์ config ถูกแก้จนเป็น JSON ผิดรูปแบบ → `json.JSONDecodeError` หลุดออกมาทำให้โปรแกรม **ล่มตั้งแต่ start** โดยไม่มี log ช่วยบอก ควรครอบ try-except แล้ว fallback เป็น `default_config` พร้อม log เตือน

---

## ⚪ ปัญหาที่ 8 — โค้ดส่วนเกิน (cosmetic)

`LogLibrary.py`:
- บรรทัด 28: `global script_dir` ที่ระดับ module ไม่มีผลอะไร (ลบได้)
- บรรทัด 60: `default_config = default_config` กำหนดค่าให้ตัวเอง ไม่มีความหมาย (ลบได้)

---

## ✅ จุดที่เขียนได้ดีอยู่แล้ว
- **กัน path traversal** ครบ 2 ชั้น (`_clean_relative_path` + `_safe_join` ด้วย `os.path.commonpath`) — ดีมาก
- ใช้ `asyncio.to_thread` กับ `os.path.isfile` เพื่อไม่บล็อก event loop — ถูกต้อง
- ตรวจหลาย mount พร้อมกันด้วย `asyncio.gather(..., return_exceptions=True)` — robust
- redirect logging ของ uvicorn เข้า loguru เป็นแนวทางที่ถูก (เหลือแค่แก้ `depth`)

---

## ลำดับการแก้ที่แนะนำ
1. **(ปัญหา 1)** เพิ่ม `enqueue=True, catch=True` ใน file sink + ยืนยันว่ารัน process เดียว → แก้อาการ log ตัน/หมุนไม่ได้
2. **(ปัญหา 2)** แก้ชื่อ entry ใน `Server.spec` ให้ build ได้
3. **(ปัญหา 3)** ตัดสินใจเรื่อง `Max_Workers` (ลบทิ้ง หรือทำให้ใช้งานจริงพร้อมแก้ logging)
4. ปัญหา 4-8 เก็บกวาดตามสะดวก
