"""Multi-worker launcher สำหรับ Linux/Docker — หลาย worker + log "ไฟล์เดียว" ผ่าน queue.

หลักการ:
  1) ตอน import HTTP_Image_Server (ใน parent) -> Loguru ถูกตั้งด้วย enqueue=True
     = สร้าง "queue + writer thread เดียว" อยู่ใน parent process
  2) parent bind listening socket เดียว แล้ว fork worker N ตัวให้ accept ร่วมกัน
     (บน Linux kernel จะ load-balance การ accept ข้าม worker ให้เอง — ใช้หลาย core จริง)
  3) worker ที่ fork มา "ไม่" ตั้ง logging ใหม่ -> มันแชร์ queue ของ parent ที่ inherit มา
     เวลา worker log มันแค่ put ลง queue, ส่วน "parent" เป็นคนเดียวที่เขียนไฟล์จริง
     => log รวมเป็นไฟล์เดียว, rotation/zip ไม่ชนกัน, ลำดับข้อความถูกต้อง

ใช้เฉพาะ Linux (อาศัย fork เพื่อแชร์ทั้ง socket และ queue ข้าม process).
บน Windows ให้ใช้ .exe single-process ตามเดิม (ดู __main__ ใน HTTP_Image_Server.py).
"""
import os
import sys
import socket
import signal
import multiprocessing as mp

import uvicorn

# import นี้ทำงานใน parent: ตั้ง Loguru (enqueue=True) -> queue + writer thread อยู่ใน parent
from HTTP_Image_Server import app, config, logger, IO_THREADS


def _serve(sock: socket.socket):
    """รันใน worker (process ลูกที่ fork มา): ไม่ตั้ง logging ใหม่ ใช้ queue ที่ inherit มาจาก parent."""
    uvconf = uvicorn.Config(
        app,
        log_config=None,
        access_log=False,       # log เองใน endpoint แล้ว
        loop="auto",            # ใช้ uvloop ถ้าติดตั้ง (uvicorn[standard]) ไม่งั้น fallback asyncio
        http="auto",            # ใช้ httptools ถ้ามี
        timeout_keep_alive=15,
    )
    server = uvicorn.Server(uvconf)
    server.run(sockets=[sock])  # serve บน socket ที่ parent bind มา (แชร์ข้าม worker)


def main():
    if sys.platform.startswith("win"):
        logger.error("server_launcher รองรับเฉพาะ Linux (fork). บน Windows ใช้ .exe single-process")
        sys.exit(1)

    port = int(config.get("Port_Server", 8080))
    try:
        workers = int(config.get("Workers", 0))
    except (TypeError, ValueError):
        workers = 0
    if workers <= 0:
        workers = os.cpu_count() or 1

    # bind listening socket เดียว แล้วให้ทุก worker (fork) แชร์ accept ร่วมกัน
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", port))
    sock.listen(4096)
    sock.set_inheritable(True)

    logger.info("Starting | {} workers (shared socket, fork) | {} I/O threads/worker | "
                "log ไฟล์เดียวผ่าน queue (writer ใน parent) | port={}",
                workers, IO_THREADS, port)

    ctx = mp.get_context("fork")
    procs = [ctx.Process(target=_serve, args=(sock,), name=f"worker-{i + 1}")
             for i in range(workers)]
    for p in procs:
        p.start()

    # ส่งต่อสัญญาณปิดไปยัง worker ทุกตัว (graceful)
    def _terminate(_signum, _frame):
        for p in procs:
            if p.is_alive():
                p.terminate()
    signal.signal(signal.SIGTERM, _terminate)
    signal.signal(signal.SIGINT, _terminate)

    for p in procs:
        p.join()
    logger.info("All workers exited")


if __name__ == "__main__":
    mp.freeze_support()
    main()
