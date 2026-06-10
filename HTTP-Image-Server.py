import os
import sys
import json
import asyncio
import logging
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
import uvicorn

from LogLibrary import Load_Config, Loguru_Logging
from loguru import logger as loguru_logger  # ใช้ instance เดียวกับ LogLibrary

# ----------------------- Configuration Values -----------------------
Program_Name = "HTTP-Image-Server"
Program_Version = "1.8"  # bugfix: safe log rotation (enqueue), faster path lookup
# ---------------------------------------------------------------------

default_config = {
    # Server เดียว แต่มีได้หลาย Path (เพิ่มได้เรื่อยๆ มากกว่า 4 ได้)
    "Mapdrive": [
        {"name": "DC", "path": "C:\\\\DC"},
        {"name": "DR", "path": "C:\\\\DR"},
        {"name": "Archive", "path": "C:\\\\Archive"},
        # {"name": "Backup1", "path": "D:\\\\Backup1"},
        # {"name": "NAS", "path": "\\\\\\\\NAS01\\\\Share\\\\Images"},
    ],
    "Port_Server": 8080,
    "Max_Workers": 4,
    "log_Level": "DEBUG",
    "Log_Console": 1,
    "log_Backup": 90,
    "Log_Size": "10 MB",
}

# ✅ Load_Config ของคุณต้องรับ 2 args
config = Load_Config(default_config, Program_Name)
logger = Loguru_Logging(config, Program_Name, Program_Version)
logger.debug("Loaded configuration: {}", config)

app = FastAPI()
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
    logger.info("Request received for file: {}", file_path)

    try:
        rel = _clean_relative_path(file_path)

        if not MOUNTS:
            return JSONResponse(status_code=500, content={"message": "Mapdrive config is missing or invalid"})

        tasks = [_check_one_mount(name, root_abs, rel) for name, root_abs in MOUNTS]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        for r in results:
            if isinstance(r, Exception):
                continue
            if r:
                name, full_path = r
                logger.info("[{}] File found: {}", name, full_path)
                return FileResponse(full_path)

        return JSONResponse(status_code=404, content={"message": "Image not found"})

    except ValueError:
        return JSONResponse(status_code=400, content={"message": "Invalid path"})
    except Exception as e:
        logger.error("An unexpected error occurred: {}", e, exc_info=True)
        return JSONResponse(status_code=500, content={"message": f"An error occurred: {e}"})


if __name__ == "__main__":
    import multiprocessing as mp
    mp.freeze_support()  # ช่วยบน Windows โดยเฉพาะเวลา spawn workers

    uvicorn.run(
        app,  # ต้องตรงชื่อไฟล์จริง
        host="0.0.0.0",
        port=int(config.get("Port_Server", 8080)),
        log_config=None,
        access_log=True,
        log_level=config.get("log_Level", "info").lower(),
    )
