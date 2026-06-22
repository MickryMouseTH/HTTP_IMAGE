"""Multi-worker launcher สำหรับ Windows .exe (frozen-safe) — log "ไฟล์เดียว" ผ่าน queue.

ทำไมต้องมีไฟล์นี้ (ต่างจาก server_launcher.py ที่ใช้ fork บน Linux):
  - Windows แชร์ listening socket ข้าม process ไม่ได้ (WinError 87) → "หลาย worker บน
    port เดียว ใน .exe เดียว" ทำไม่ได้จริงบน Windows
  - ไฟล์นี้จึงทำแบบที่ทำได้จริง: .exe เดียวเป็น "parent" ที่ spawn หลาย worker process
    (คนละ port: BASE, BASE+1, ...) แล้ววาง nginx (port 50000) ไว้หน้าเพื่อรวมเป็น port เดียว

Logging (ตรงโจทย์):
  - parent สร้าง multiprocessing.Queue 1 อัน + "thread เขียน log ตัวเดียว"
  - worker ทุกตัว "ไม่" เปิดไฟล์ log เอง (env HTTP_IMAGE_WORKER=1 สั่ง HTTP_Image_Server
    ปิด sink ไฟล์/คอนโซลของตัวเอง) แต่ฟอร์แมตบรรทัด log ติด "W<หมายเลข worker>" แล้ว put เข้า queue
  - thread เดียวใน parent ดึงจาก queue มาเขียน "ไฟล์เดียว" (rotation/zip/retention โดย Loguru)
  => log รวมไฟล์เดียว, ไม่ชน file lock, ลำดับถูก, และรู้ว่าแต่ละบรรทัดมาจาก worker ไหน

หมายเหตุ frozen: build ด้วย PyInstaller แบบ --onedir (เสถียรกับ multiprocessing spawn)
ดู build-exe.bat
"""
import os
import sys
import time
import threading
import multiprocessing as mp

# ต้องตั้งก่อน import HTTP_Image_Server: ให้ทั้ง parent และ worker (ที่ inherit env ตอน spawn)
# ปิด sink อัตโนมัติของ LogLibrary — logging ทั้งหมดคุมจากไฟล์นี้ผ่าน queue กลาง
os.environ["HTTP_IMAGE_WORKER"] = "1"

import uvicorn
from loguru import logger

from HTTP_Image_Server import app, config, Program_Name, Program_Version, IO_THREADS


def _base_dir():
    """โฟลเดอร์ที่วาง config/logs — ข้าง .exe เมื่อ frozen, ไม่งั้นข้างสคริปต์."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


# ---------------------------------------------------------------------------
# WORKER (รันใน process ลูกที่ spawn มา)
# ---------------------------------------------------------------------------
def worker_main(worker_id: int, port: int, log_q):
    """ตั้ง logging ให้ส่งเข้า queue (ติด worker id) แล้วรัน uvicorn single-process บน port นี้."""
    mp.freeze_support()

    log_level = str(config.get("log_Level", "INFO")).upper()
    logger.remove()
    logger.configure(extra={"worker": worker_id})
    # Loguru จะส่ง "ข้อความที่ฟอร์แมตเสร็จ (รวม \n)" เข้า callable sink -> เรา put ลง queue กลาง
    fmt = ("{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | W{extra[worker]:02d} | "
           "tid:{thread.id} | {function} | {message}")
    logger.add(lambda m: log_q.put(str(m)), level=log_level, format=fmt, catch=True)

    logger.info("Worker up | port={} | {} I/O threads", port, IO_THREADS)
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=port,
        log_config=None,
        access_log=False,
        loop="auto",
        http="auto",
        backlog=4096,
        timeout_keep_alive=15,
        log_level=str(config.get("log_Level", "info")).lower(),
    )


# ---------------------------------------------------------------------------
# PARENT — thread เขียน log ตัวเดียว + supervisor
# ---------------------------------------------------------------------------
def _log_writer(log_q):
    """thread เดียวใน parent: ดึงบรรทัด log จากทุก worker (ผ่าน queue) แล้วเขียน "ไฟล์เดียว"."""
    log_dir = os.path.join(_base_dir(), "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, f"{Program_Name}_{Program_Version}.log")

    log_level = str(config.get("log_Level", "INFO")).upper()
    log_size = str(config.get("Log_Size", "10 MB")).upper()
    log_backup = int(config.get("log_Backup", 90))

    logger.remove()
    if int(config.get("Log_Console", 1)) == 1:
        logger.add(sys.stdout, level=log_level, format="{message}", colorize=False)
    if int(config.get("Log_File", 1)) == 1:
        logger.add(
            log_file,
            level=log_level,
            format="{message}",
            rotation=log_size,
            retention=f"{log_backup} days",
            compression="zip",
            enqueue=False,   # มี thread นี้ตัวเดียวที่เขียนอยู่แล้ว ไม่ต้องคิวซ้อน
            catch=True,
        )

    # worker ฟอร์แมตบรรทัดมาครบแล้ว (รวม worker id + \n) -> เขียนตามจริงแบบ raw
    sink = logger.opt(raw=True)
    while True:
        item = log_q.get()
        if item is None:        # sentinel = ปิด
            break
        sink.info(item)


def main():
    if not sys.platform.startswith("win"):
        print("server_launcher_win ออกแบบสำหรับ Windows .exe — บน Linux ใช้ server_launcher.py (fork)",
              file=sys.stderr)

    try:
        workers = int(config.get("Workers", 0))
    except (TypeError, ValueError):
        workers = 0
    if workers <= 0:
        workers = os.cpu_count() or 1

    base_port = int(config.get("Worker_Base_Port", 50001))

    ctx = mp.get_context("spawn")     # Windows ใช้ spawn เสมอ (ไม่มี fork)
    log_q = ctx.Queue(-1)

    writer = threading.Thread(target=_log_writer, args=(log_q,), name="log-writer", daemon=True)
    writer.start()

    # banner ผ่าน queue ให้รวมอยู่ในไฟล์เดียวกัน
    bar = "-" * 100
    log_q.put(bar + "\n")
    log_q.put(f"Start {Program_Name} {Program_Version} | {workers} workers (Windows .exe, spawn) | "
              f"ports {base_port}-{base_port + workers - 1} | log ไฟล์เดียวผ่าน queue (writer thread เดียว)\n")
    log_q.put(bar + "\n")

    def _spawn(wid, port):
        p = ctx.Process(target=worker_main, args=(wid, port, log_q), name=f"worker-{wid}")
        p.start()
        return p

    procs = []
    for i in range(workers):
        procs.append([i + 1, base_port + i, _spawn(i + 1, base_port + i)])

    # supervisor: worker ตาย -> restart (รักษาจำนวน worker ให้ครบ)
    try:
        while True:
            time.sleep(1.0)
            for entry in procs:
                wid, port, p = entry
                if not p.is_alive():
                    log_q.put(f"{Program_Name} | [supervisor] worker W{wid:02d} (port {port}) died "
                              f"(exit={p.exitcode}) -> restarting\n")
                    entry[2] = _spawn(wid, port)
    except KeyboardInterrupt:
        log_q.put(f"{Program_Name} | [supervisor] shutting down {len(procs)} workers\n")
        for _, _, p in procs:
            if p.is_alive():
                p.terminate()
        for _, _, p in procs:
            p.join(timeout=10)
    finally:
        log_q.put(None)           # ปิด writer
        writer.join(timeout=5)


if __name__ == "__main__":
    mp.freeze_support()           # ต้องมาก่อนสุดใน __main__ (ปลอดภัยกับ .exe + spawn)
    main()
