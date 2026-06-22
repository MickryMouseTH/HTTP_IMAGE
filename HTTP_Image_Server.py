import os
import sys
import json
import time
import asyncio
import logging
from contextlib import asynccontextmanager
from concurrent.futures import ThreadPoolExecutor
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
import uvicorn

from LogLibrary import Load_Config, Loguru_Logging
from loguru import logger as loguru_logger  # ใช้ instance เดียวกับ LogLibrary

# ----------------------- Configuration Values -----------------------
Program_Name = "HTTP-Image-Server"
Program_Version = "2.6"  # Linux/Docker multi-core (uvloop+workers) when not frozen; immutable cache
# ---------------------------------------------------------------------

default_config = {
    # Server เดียว แต่มีได้หลาย Path (เพิ่มได้เรื่อยๆ มากกว่า 4 ได้)
    "Mapdrive": [
        {"name": "DC", "path": "C:\\DC"},
        {"name": "DR", "path": "C:\\DR"},
        {"name": "Archive", "path": "C:\\Archive"},
        # local disk:    {"name": "Backup1", "path": "D:\\Backup1"},
        # network share:  {"name": "NAS", "path": "\\\\172.30.54.1\\image\\"},
    ],
    "Port_Server": 8080,
    "Max_Workers": 64,           # จำนวน I/O thread ต่อ process. share ช้า/โหลดสูง -> เพิ่มเป็น 128-256
    "Workers": 0,                # จำนวน process (multi-core). 0 = auto = os.cpu_count().
                                 #   - รันเป็น .py ตรง ๆ (Linux/Docker): uvicorn workers
                                 #   - .exe จาก server_launcher_win.py: จำนวน worker process ที่ spawn
                                 #   - .exe จาก HTTP_Image_Server.py (frozen เดิม): บังคับ 1 process เสมอ
    "Worker_Base_Port": 50001,   # .exe multi-worker: worker ตัวแรกใช้ port นี้ แล้วไล่ +1 (50001, 50002, ...)
                                 #   วาง nginx (nginx.windows.conf, port 50000) ไว้หน้า cluster
    "Cache_Max_Age": 31536000,   # อายุ cache ของรูป (วินาที). รูป immutable -> ตั้งยาวได้ (1 ปี)
    "Cache_Immutable": 1,        # 1 = ใส่ directive `immutable` (client ไม่ revalidate เลย — รูปไม่เคยเปลี่ยน)
    "log_Level": "INFO",         # prod ใช้ INFO; DEBUG เฉพาะตอนไล่ปัญหา (DEBUG = ~6 บรรทัด/req กิน throughput)
    "Log_Console": 1,            # 3000-4000 req/s แนะนำตั้ง 0 (เขียน stdout ทุกบรรทัดเป็นคอขวด)
    "Log_File": 1,               # 1 = เขียนไฟล์ log. ใน Docker หลาย worker แนะนำตั้ง 0 + Log_Console=1
                                 #   (ให้ Docker เก็บ stdout แทน เลี่ยงหลาย process หมุนไฟล์ชนกัน)
    "log_Backup": 90,
    "Log_Size": "10 MB",
}

# ✅ Load_Config ของคุณต้องรับ 2 args
config = Load_Config(default_config, Program_Name)

# Multi-instance บน Windows: รันหลาย instance คนละ port (8080, 8081, ...) หน้า reverse proxy
# (nginx) เพื่อใช้หลาย core — Windows แชร์ listening socket ข้าม process ไม่ได้ (WinError 87)
# ตั้ง env `HTTP_IMAGE_PORT` ต่อ instance:
#   - override port ที่ bind (ดูใน __main__)
#   - แยกไฟล์ log ต่อ instance -> เลี่ยงหลาย process แย่งหมุน/zip ไฟล์ log ตัวเดียวกัน (WinError 32)
_INSTANCE_PORT = os.environ.get("HTTP_IMAGE_PORT", "").strip()
_log_tag = f"{Program_Version}_{_INSTANCE_PORT}" if _INSTANCE_PORT else Program_Version

# โหมด worker (ถูกสตาร์ทโดย server_launcher_win.py): ปิด sink อัตโนมัติของ LogLibrary
# เพราะ launcher จะตั้ง sink ส่ง log เข้า "queue กลาง" แล้วให้ thread เดียวใน parent เขียนไฟล์เดียว
# (กันหลาย process แย่งหมุน/zip ไฟล์ log ตัวเดียวกัน — WinError 32). ไม่แตะ config ตัวจริงของ module
_log_cfg = config
if os.environ.get("HTTP_IMAGE_WORKER") == "1":
    _log_cfg = {**config, "Log_File": 0, "Log_Console": 0}
logger = Loguru_Logging(_log_cfg, Program_Name, _log_tag)
logger.debug("Loaded configuration: {}", config)

# Max_Workers = จำนวน I/O thread (concurrency สำหรับงานที่ block: เช็คไฟล์ + อ่านไฟล์ส่งกลับ)
# รันแบบ process เดียว (เป็นวิธีเดียวที่ .exe ตัวเดียวบน Windows เสิร์ฟ port เดียวได้แน่นอน
# เพราะ Windows แชร์ listening socket ข้าม process ไม่ได้ -> IOCP error WinError 87)
# ยิ่ง share ช้า ยิ่งควรเพิ่มค่านี้ (แต่ละ thread รอ network ได้พร้อมกัน)
try:
    IO_THREADS = max(8, int(config.get("Max_Workers", 64)))
except (TypeError, ValueError):
    IO_THREADS = 64


@asynccontextmanager
async def lifespan(_app: "FastAPI"):
    """ขยาย threadpool ของแต่ละ process ตอน startup ให้รับงาน I/O พร้อมกันได้มากขึ้น.

    - asyncio default executor: ใช้โดย asyncio.to_thread (os.path.isfile)
    - anyio thread limiter:     ใช้โดย FileResponse ตอนอ่านไฟล์ส่งกลับ
    ทั้งคู่ default ค่อนข้างต่ำ (~32-40) จึงตั้งให้สูงขึ้นเป็น IO_THREADS
    """
    loop = asyncio.get_running_loop()
    loop.set_default_executor(
        ThreadPoolExecutor(max_workers=IO_THREADS, thread_name_prefix="io")
    )
    try:
        import anyio
        anyio.to_thread.current_default_thread_limiter().total_tokens = IO_THREADS
    except Exception as ex:  # pragma: no cover - กันกรณี anyio เปลี่ยน API
        logger.warning("ปรับ anyio thread limiter ไม่ได้: {}", ex)
    logger.debug("I/O thread pool ready | threads_per_proc={}", IO_THREADS)
    yield


app = FastAPI(lifespan=lifespan)
MAPDRIVE = config.get("Mapdrive", [])


def _normalize_mounts(mapdrive):
    """แปลง config Mapdrive เป็น list ของ (name, abs_root) ครั้งเดียวตอน start.

    เพื่อเลี่ยงการเรียก os.path.abspath() ซ้ำทุก request (ลด syscall ต่อคำขอ).
    """
    mounts = []
    if isinstance(mapdrive, list):
        for i, m in enumerate(mapdrive):
            if not isinstance(m, dict):
                continue
            root = m.get("path")
            if not root:
                continue
            name = m.get("name", f"path{i + 1}")
            mounts.append((name, os.path.abspath(root)))
    return mounts


# Precompute ครั้งเดียวตอนโหลดโมดูล
MOUNTS = _normalize_mounts(MAPDRIVE)

# ค่า cache สำหรับรูป (วินาที) — อ่านครั้งเดียว ไม่ต้องอ่านซ้ำทุก request
CACHE_MAX_AGE = int(config.get("Cache_Max_Age", 3600))
_cache_control = f"public, max-age={CACHE_MAX_AGE}"
if int(config.get("Cache_Immutable", 0)) == 1:
    # รูป immutable (ไม่เคยทับชื่อเดิม) -> client/nginx ไม่ต้อง revalidate เลย
    _cache_control += ", immutable"
_CACHE_HEADERS = {"Cache-Control": _cache_control}

# สรุปการตั้งค่าตอน start (INFO เห็นจำนวน, DEBUG เห็น path เต็มของแต่ละ mount)
logger.info("Loaded {} mount(s): {} | Cache-Control max-age={}s",
            len(MOUNTS), [name for name, _ in MOUNTS], CACHE_MAX_AGE)
if not MOUNTS:
    logger.warning("No usable mount configured — every /image request will return 500")


def _probe_mounts():
    """ตรวจว่าแต่ละ mount root เข้าถึงได้จริงไหมตอน startup.

    ช่วยดีบักเคส "เรียกไม่เจอ" โดยตรง โดยเฉพาะ network share (UNC) ที่
    เครื่องอาจไม่มีสิทธิ์/เน็ตเข้าไม่ถึง -> ถ้าเข้าไม่ได้จะ log WARNING ชัดเจน
    แทนที่จะเงียบแล้วตอบ 404 ทุก request.
    """
    for name, root in MOUNTS:
        try:
            reachable = os.path.isdir(root)
        except Exception as ex:
            logger.warning("Mount [{}] probe ERROR | {} | {}", name, root, ex)
            continue
        if reachable:
            logger.info("Mount [{}] OK -> {}", name, root)
        else:
            logger.warning(
                "Mount [{}] NOT accessible -> {} "
                "(network share ล่ม? path ผิด? account ไม่มีสิทธิ์เข้า share?)",
                name, root)


_probe_mounts()


# ----------------------- Uvicorn -> Loguru Redirect -----------------------
class InterceptHandler(logging.Handler):
    def emit(self, record: logging.LogRecord):
        try:
            level = loguru_logger.level(record.levelname).name
        except Exception:
            level = record.levelno

        # ไล่ stack กลับไปหา caller จริง เพื่อให้ field {function} ใน log
        # ไม่โชว์เป็น "emit" ทุกบรรทัด
        frame, depth = logging.currentframe(), 2
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1

        loguru_logger.opt(depth=depth, exception=record.exc_info).log(
            level, record.getMessage()
        )


def setup_uvicorn_to_loguru():
    """
    ปิด console logging ของ uvicorn (ไม่ให้ uvicorn config logger เอง)
    แล้ว redirect logs ของ uvicorn/standard logging ทั้งหมด เข้า Loguru
    """
    intercept = InterceptHandler()

    # รีเซ็ต root handlers
    root = logging.getLogger()
    root.handlers = []
    root.setLevel(logging.INFO)
    root.addHandler(intercept)

    # ทำให้ uvicorn logger ต่างๆ ส่งต่อไป root
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        l = logging.getLogger(name)
        l.handlers = []
        l.propagate = True
        l.setLevel(logging.INFO)


setup_uvicorn_to_loguru()
# ---------------------------------------------------------------------

def _find_in_mounts(rel: str):
    """ค้นไฟล์ rel ในทุก mount ตามลำดับความสำคัญ — รันใน "เธรดเดียว" ต่อ 1 คำขอ.

    เหตุผลที่ทำในเธรดเดียว (เปลี่ยนจาก v2.4 ที่ยิง 1 เธรด/mount):
    ที่ 3000-4000 req/s การ dispatch N เธรด/คำขอ (N = จำนวน mount) ทำให้ thread pool
    และ GIL แย่งกันหนัก (4000 req/s × 3 mount = 12,000 dispatch/s). การค้นแบบ early-exit
    ตามลำดับความสำคัญอยู่แล้วทำให้เคสปกติ (ไฟล์อยู่ mount แรก) เสีย isfile แค่ครั้งเดียว
    และไม่แตะ mount ช้าที่อยู่ท้าย ๆ เลย -> ทั้งเร็วกว่าและกินทรัพยากรน้อยกว่า

    คืน (name, full_path) ของ mount แรกที่เจอ หรือ None.
    """
    for name, root_abs in MOUNTS:
        # root_abs ถูก abspath ไว้แล้วตอน start และ rel ผ่าน _clean_relative_path
        # (ไม่มี '..' / ไม่ใช่ absolute) -> join ได้ path ที่อยู่ใต้ root แน่นอน
        full_path = os.path.join(root_abs, rel)

        # กัน path traversal อีกชั้น (เผื่อ symlink/edge case): ไฟล์ต้องอยู่ใต้ root จริง
        try:
            if os.path.commonpath([root_abs, full_path]) != root_abs:
                continue
        except ValueError:
            # คนละ drive บน Windows -> ไม่ปลอดภัย ข้าม mount นี้
            continue

        try:
            if os.path.isfile(full_path):
                return (name, full_path)
        except OSError:
            # mount เข้าไม่ถึงชั่วคราว (network share ล่ม) -> ข้ามไป mount ถัดไป
            continue
    return None


@app.get("/")
def read_root():
    logger.info("Root endpoint accessed.")
    return {
        "message": "Image server is running with configured mount paths.",
        "image_path": "/image/{file_path}",
        "mounts": [name for name, _ in MOUNTS],
    }


def _clean_relative_path(p: str) -> str:
    p = p.lstrip("/\\")
    p = os.path.normpath(p)

    # กัน path traversal / absolute path
    if p.startswith("..") or os.path.isabs(p):
        raise ValueError("Invalid path")
    return p


@app.get("/image/{file_path:path}")
async def get_image(file_path: str):
    # ระดับ log แยกตาม Log Level:
    #   DEBUG   = เห็นทุกขั้น (รับ request, normalize path, ผลเช็คแต่ละ mount)
    #   INFO    = สรุป 1 บรรทัด/request ที่สำเร็จ (status, mount, เวลา)
    #   WARNING = 404 / path ไม่ถูกต้อง (400)
    #   ERROR   = ข้อผิดพลาดที่ไม่คาดคิด (500)
    start = time.perf_counter()
    logger.debug("Request received | raw_path={!r}", file_path)

    try:
        rel = _clean_relative_path(file_path)
        logger.debug("Normalized relative path | rel={!r}", rel)

        if not MOUNTS:
            logger.error("Mapdrive config is missing or invalid (no usable mounts)")
            return JSONResponse(status_code=500, content={"message": "Mapdrive config is missing or invalid"})

        # ค้นทุก mount ตามลำดับความสำคัญในเธรดเดียว (early-exit เจอ mount แรกก็หยุด)
        # -> ไม่บล็อก event loop และใช้แค่ 1 เธรด/คำขอ (ดูเหตุผลใน _find_in_mounts)
        hit = await asyncio.to_thread(_find_in_mounts, rel)

        if hit:
            found_name, full_path = hit
            elapsed_ms = (time.perf_counter() - start) * 1000
            logger.debug("[{}] HIT | full_path={}", found_name, full_path)
            logger.info("200 OK | mount={} | rel={!r} | {:.1f} ms",
                        found_name, rel, elapsed_ms)
            return FileResponse(full_path, headers=_CACHE_HEADERS)

        elapsed_ms = (time.perf_counter() - start) * 1000
        logger.warning("404 Not Found | rel={!r} | searched {} mount(s) | {:.1f} ms",
                       rel, len(MOUNTS), elapsed_ms)
        return JSONResponse(status_code=404, content={"message": "Image not found"})

    except ValueError:
        logger.warning("400 Bad Request | invalid path | raw_path={!r}", file_path)
        return JSONResponse(status_code=400, content={"message": "Invalid path"})
    except Exception as e:
        elapsed_ms = (time.perf_counter() - start) * 1000
        logger.error("500 Internal Error | raw_path={!r} | {:.1f} ms | {}",
                     file_path, elapsed_ms, e)
        logger.opt(exception=True).debug("Traceback for 500 error")
        return JSONResponse(status_code=500, content={"message": f"An error occurred: {e}"})


if __name__ == "__main__":
    import multiprocessing as mp
    mp.freeze_support()  # ปลอดภัยกับ .exe (frozen build)

    # env HTTP_IMAGE_PORT (ตั้งโดย start-cluster.bat ต่อ instance) ทับค่าใน config
    port = int(_INSTANCE_PORT or config.get("Port_Server", 8080))
    log_level = config.get("log_Level", "info").lower()
    frozen = getattr(sys, "frozen", False)

    # พารามิเตอร์ที่ใช้ร่วมกันทั้งสองโหมด
    common = dict(
        host="0.0.0.0",
        port=port,
        log_config=None,
        access_log=False,       # เก็บ log เองใน endpoint แล้ว ไม่ต้องให้ uvicorn log ซ้ำทุก request
        log_level=log_level,
        backlog=4096,           # คิว accept ของ socket (default 2048 อาจล้นช่วงพีค -> client โดน refused)
        timeout_keep_alive=15,  # คง connection ไว้ reuse (เลี่ยง TCP handshake ทุกคำขอ)
    )

    if frozen:
        # ----- โหมด .exe (Windows): process เดียวเสมอ -----
        # เป็นวิธีเดียวที่ .exe ตัวเดียวเสิร์ฟ port เดียวบน Windows ได้แน่นอน
        # (Windows แชร์ listening socket ข้าม process ไม่ได้ -> WinError 87)
        # ต้องการ multi-core บน Windows: รันหลาย instance คนละ port + reverse proxy (IIS/nginx)
        logger.info("Starting | frozen single-process | {} I/O threads | port={}", IO_THREADS, port)
        uvicorn.run(app, **common)
    else:
        # ----- โหมด script ปกติ (Linux/Docker): ใช้ได้หลาย core -----
        # workers>1 -> uvloop + httptools (auto ถ้าติดตั้ง uvicorn[standard]) + แชร์ socket ผ่าน
        # supervisor ของ uvicorn เอง (บน Linux ใช้ SO_REUSEPORT ได้ ไม่ติดข้อจำกัด Windows)
        try:
            workers = int(config.get("Workers", 0))
        except (TypeError, ValueError):
            workers = 0
        if workers <= 0:
            workers = os.cpu_count() or 1

        if workers > 1:
            logger.info("Starting | {} workers (multi-core) | {} I/O threads/worker | port={}",
                        workers, IO_THREADS, port)
            # ต้องส่ง app เป็น import-string เพื่อให้ uvicorn spawn worker แล้ว re-import ได้
            uvicorn.run("HTTP_Image_Server:app", workers=workers,
                        loop="auto", http="auto", **common)
        else:
            logger.info("Starting | single-process (non-frozen) | {} I/O threads | port={}",
                        IO_THREADS, port)
            uvicorn.run(app, loop="auto", http="auto", **common)
