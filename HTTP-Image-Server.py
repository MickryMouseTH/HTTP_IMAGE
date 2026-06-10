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
Program_Version = "1.7"  # updated for uvicorn logging redirect
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


# ----------------------- Uvicorn -> Loguru Redirect -----------------------
class InterceptHandler(logging.Handler):
    def emit(self, record: logging.LogRecord):
        try:
            level = loguru_logger.level(record.levelname).name
        except Exception:
            level = record.levelno

        loguru_logger.opt(exception=record.exc_info).log(level, record.getMessage())


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

async def _check_one_mount(item, rel, i):
    if not isinstance(item, dict):
        return None

    name = item.get("name", f"path{i+1}")
    root = item.get("path")
    if not root:
        return None

    full_path = _safe_join(root, rel)
    if await _file_exists(full_path):
        return (name, full_path)
    return None


@app.get("/")
def read_root():
    logger.info("Root endpoint accessed.")
    mounts = []
    if isinstance(MAPDRIVE, list):
        for i, m in enumerate(MAPDRIVE):
            if isinstance(m, dict):
                mounts.append(m.get("name", f"path{i+1}"))
    return {
        "message": "Image server is running with configured mount paths.",
        "image_path": "/image/{file_path}",
        "mounts": mounts,
    }


def _clean_relative_path(p: str) -> str:
    p = p.lstrip("/\\")
    p = os.path.normpath(p)

    # กัน path traversal / absolute path
    if p.startswith("..") or os.path.isabs(p):
        raise ValueError("Invalid path")
    return p


def _safe_join(root: str, rel: str) -> str:
    root_abs = os.path.abspath(root)
    full = os.path.abspath(os.path.join(root_abs, rel))

    if os.path.commonpath([root_abs, full]) != root_abs:
        raise ValueError("Path escapes root")
    return full


async def _file_exists(path: str) -> bool:
    return await asyncio.to_thread(os.path.isfile, path)


@app.get("/image/{file_path:path}")
async def get_image(file_path: str):
    logger.info("Request received for file: {}", file_path)

    try:
        rel = _clean_relative_path(file_path)

        if not isinstance(MAPDRIVE, list) or not MAPDRIVE:
            return JSONResponse(status_code=500, content={"message": "Mapdrive config is missing or invalid"})

        tasks = []
        for i, item in enumerate(MAPDRIVE):
            tasks.append(_check_one_mount(item, rel, i))

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
        port=int(config.get("Port_Server", 50000)),
        log_config=None,
        access_log=True,
        log_level=config.get("log_Level", "info").lower(),
    )
