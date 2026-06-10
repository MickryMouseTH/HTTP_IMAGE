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
Program_Version = "2.3"  # true multi-process via shared socket (frozen-safe); Max_Workers = processes
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
    "Max_Workers": 4,            # จำนวน worker process (กระจายข้าม CPU core); แต่ละ process มี 64 I/O thread
    "Cache_Max_Age": 3600,       # อายุ cache ของรูป (วินาที) ส่งใน Cache-Control header
    "log_Level": "DEBUG",
    "Log_Console": 1,
    "log_Backup": 90,
    "Log_Size": "10 MB",
}

# ✅ Load_Config ของคุณต้องรับ 2 args
config = Load_Config(default_config, Program_Name)
logger = Loguru_Logging(config, Program_Name, Program_Version)
logger.debug("Loaded configuration: {}", config)

# Max_Workers = จำนวน worker "process" จริง (กระจายงานข้าม CPU core = throughput สูงสุด)
# รองรับ .exe เพราะ spawn ผ่าน multiprocessing + freeze_support (ไม่ใช่ uvicorn --workers ที่ crash)
try:
    WORKER_PROCESSES = max(1, int(config.get("Max_Workers", 4)))
except (TypeError, ValueError):
    WORKER_PROCESSES = 4

# จำนวน I/O thread ต่อ 1 process (สำหรับงานที่ block: เช็คไฟล์ + อ่านไฟล์ส่งกลับ)
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
_CACHE_HEADERS = {"Cache-Control": f"public, max-age={CACHE_MAX_AGE}"}

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

async def _check_one_mount(name: str, root_abs: str, rel: str):
    """ตรวจว่าไฟล์ rel มีอยู่ใน mount นี้ไหม (root_abs ถูก precompute มาแล้ว)."""
    full_path = os.path.abspath(os.path.join(root_abs, rel))

    # กัน path traversal: ไฟล์ต้องอยู่ใต้ root จริง
    try:
        if os.path.commonpath([root_abs, full_path]) != root_abs:
            return None
    except ValueError:
        # คนละ drive บน Windows -> ไม่ปลอดภัย
        return None

    if await _file_exists(full_path):
        return (name, full_path)
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


async def _file_exists(path: str) -> bool:
    return await asyncio.to_thread(os.path.isfile, path)


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

        # สั่งเช็คทุก mount พร้อมกัน (concurrent) แต่ await ตามลำดับความสำคัญ
        # -> เจอใน mount แรกก็คืนทันที ไม่ต้องรอ mount ช้า (เช่น NAS) ที่อยู่ท้าย ๆ
        tasks = [asyncio.create_task(_check_one_mount(name, root_abs, rel))
                 for name, root_abs in MOUNTS]
        try:
            for (name, _root), t in zip(MOUNTS, tasks):
                try:
                    r = await t
                except Exception as ex:
                    logger.debug("[{}] check error | {}", name, ex)
                    r = None

                if r:
                    found_name, full_path = r
                    elapsed_ms = (time.perf_counter() - start) * 1000
                    logger.debug("[{}] HIT | full_path={}", found_name, full_path)
                    logger.info("200 OK | mount={} | rel={!r} | {:.1f} ms",
                                found_name, rel, elapsed_ms)
                    return FileResponse(full_path, headers=_CACHE_HEADERS)

                logger.debug("[{}] miss", name)

            elapsed_ms = (time.perf_counter() - start) * 1000
            logger.warning("404 Not Found | rel={!r} | searched {} mount(s) | {:.1f} ms",
                           rel, len(MOUNTS), elapsed_ms)
            return JSONResponse(status_code=404, content={"message": "Image not found"})
        finally:
            # ยกเลิก task ที่ยังค้าง (เช่น mount ช้าที่ไม่ต้องรอแล้ว)
            for t in tasks:
                if not t.done():
                    t.cancel()

    except ValueError:
        logger.warning("400 Bad Request | invalid path | raw_path={!r}", file_path)
        return JSONResponse(status_code=400, content={"message": "Invalid path"})
    except Exception as e:
        elapsed_ms = (time.perf_counter() - start) * 1000
        logger.error("500 Internal Error | raw_path={!r} | {:.1f} ms | {}",
                     file_path, elapsed_ms, e)
        logger.opt(exception=True).debug("Traceback for 500 error")
        return JSONResponse(status_code=500, content={"message": f"An error occurred: {e}"})


def _run_uvicorn(sockets=None):
    """รัน uvicorn 1 process. ถ้าส่ง sockets มา = ใช้ socket ที่ parent bind ไว้ร่วมกัน."""
    cfg = uvicorn.Config(
        app,
        host="0.0.0.0",
        port=int(config.get("Port_Server", 8080)),
        log_config=None,
        access_log=False,  # เก็บ log เองใน endpoint แล้ว ไม่ต้องให้ uvicorn log ซ้ำทุก request
        log_level=config.get("log_Level", "info").lower(),
    )
    uvicorn.Server(cfg).run(sockets=sockets)


if __name__ == "__main__":
    import socket
    import multiprocessing as mp
    mp.freeze_support()  # ⭐ จำเป็นสำหรับ .exe (PyInstaller) ตอน spawn worker

    port = int(config.get("Port_Server", 8080))

    if WORKER_PROCESSES <= 1:
        logger.info("Starting | 1 process | {} I/O threads | port={}", IO_THREADS, port)
        _run_uvicorn()
    else:
        # bind socket เดียว แล้วให้ทุก worker process accept ร่วมกัน (kernel load-balance)
        # ต่างจาก uvicorn --workers: เรา spawn เองด้วย multiprocessing.Process + freeze_support
        # จึงรันใน .exe ได้โดยไม่ crash loop และทุก process ใช้ port เดียวกันได้ (socket แชร์)
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("0.0.0.0", port))
        sock.listen(2048)
        sock.set_inheritable(True)

        logger.info("Starting | {} worker processes (shared port {}) | {} I/O threads/proc",
                    WORKER_PROCESSES, port, IO_THREADS)
        procs = []
        for i in range(WORKER_PROCESSES):
            p = mp.Process(target=_run_uvicorn, kwargs={"sockets": [sock]},
                           name=f"worker-{i + 1}", daemon=False)
            p.start()
            procs.append(p)
            logger.info("  worker-{} started | pid={}", i + 1, p.pid)

        # parent ไม่ต้อง accept เอง: ปิด copy ของ socket ทิ้ง (worker มี handle ของตัวเองแล้ว)
        sock.close()
        try:
            for p in procs:
                p.join()
        except KeyboardInterrupt:
            logger.info("Shutting down {} workers...", len(procs))
            for p in procs:
                p.terminate()
            for p in procs:
                p.join()
