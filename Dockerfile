# ---- HTTP-Image-Server v2.6 | Linux/Docker (multi-core + uvloop + httptools) ----
# ภาพนี้ "ไม่ frozen" จึงปลดล็อก multi-core workers + uvloop ที่ Windows .exe ทำไม่ได้
# config "ไฟล์เดียว" (HTTP-Image-Server_config.json): entrypoint อ่านแล้ว mount SMB ให้เอง
FROM python:3.12-slim

# cifs-utils = mount SMB share ในคอนเทนเนอร์ (รันเป็น root ตัวเดียว ไม่ต้อง gosu)
RUN apt-get update \
    && apt-get install -y --no-install-recommends cifs-utils \
    && rm -rf /var/lib/apt/lists/*

# 1) deps (cache layer แยกจาก source เพื่อ rebuild เร็ว)
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 2) source + entrypoint
# (chmod ให้ทุก user อ่านได้ + entrypoint รันได้ — เผื่อไฟล์ต้นทางบน host เป็น mode 700)
COPY HTTP_Image_Server.py LogLibrary.py server_launcher.py secret_util.py entrypoint.sh ./
RUN chmod 644 HTTP_Image_Server.py LogLibrary.py server_launcher.py secret_util.py && chmod 755 entrypoint.sh

# 3) dir สำหรับ logs/secret (host bind mount ทับอีกที; รันเป็น root จึงไม่ต้อง chown)
RUN mkdir -p /app/logs /app/secret

EXPOSE 8080

# entrypoint (root): mount CIFS จาก config -> รัน launcher
# launcher: fork หลาย worker แชร์ socket (multi-core) + log "ไฟล์เดียว" ผ่าน queue (writer ใน parent)
ENTRYPOINT ["/app/entrypoint.sh"]
