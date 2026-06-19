#!/bin/sh
# entrypoint: อ่าน SMB mount จาก config "ไฟล์เดียว" (HTTP-Image-Server_config.json)
# แล้ว mount CIFS ให้เองตอนบูต container -> จากนั้น drop เป็น appuser แล้วรัน launcher
#
# ต้องรันด้วยสิทธิ์ root (mount CIFS ต้องใช้) + container ต้องได้ cap SYS_ADMIN
# ทุกค่า (share, user/pass, path, options) มาจาก config ไฟล์เดียว -> ไม่ต้องแก้ที่อื่น
set -e

CONFIG=/app/HTTP-Image-Server_config.json

# แยกข้อมูล mount ออกจาก config ด้วย python (มีในอิมเมจอยู่แล้ว) -> เขียนไฟล์ชั่วคราว
# get_password_and_seal: ถ้า Smb_Pass เป็น plaintext (รันครั้งแรก) -> เข้ารหัสเขียนกลับ config เป็น enc:
#                        ถ้าเป็น enc: อยู่แล้ว -> ถอดรหัสกลับมาใช้ (คีย์อยู่ที่ /app/secret/cifs.key)
python - "$CONFIG" <<'PY'
import json, os, sys
sys.path.insert(0, "/app")
from secret_util import get_password_and_seal

cfg_path = sys.argv[1]
pw, cfg = get_password_and_seal(cfg_path)   # pw = plaintext (decrypt/seal อัตโนมัติ)
domain = cfg.get("Smb_Domain", "")          # โดเมน AD (samAccountName + domain แยกกัน)
user = cfg.get("Smb_User", "")
opts = cfg.get("Smb_Options", "vers=3.1.1,sec=ntlmssp,cache=loose,actimeo=600,rsize=4194304,wsize=4194304")

# credentials file (โหมด 600) -> เลี่ยง user/pass โผล่ใน process list (ps/mount)
# AD: ใส่บรรทัด domain= ด้วย (ถ้าไม่ใส่ kerberos/ntlm จะ auth ไม่ผ่านกับ domain account)
with open("/tmp/smb.cred", "w") as f:
    f.write(f"username={user}\npassword={pw}\n")
    if domain:
        f.write(f"domain={domain}\n")
os.chmod("/tmp/smb.cred", 0o600)

with open("/tmp/smb.opts", "w") as f:
    f.write(opts)

def norm_smb(s):
    # รองรับใส่แบบ Windows UNC (\\server\share\) -> แปลงเป็นรูปแบบ Linux cifs (//server/share)
    s = s.replace("\\", "/")
    return "//" + s.strip("/")                 # ตัด slash หัว/ท้ายแล้วเติม // นำหน้า

with open("/tmp/mounts.tsv", "w") as f:
    for m in cfg.get("Mapdrive", []):
        smb, path = m.get("smb"), m.get("path")
        if smb and path:                       # mount เฉพาะรายการที่ระบุ smb (ไม่ระบุ = ใช้ path ตามมีตามเกิด)
            f.write(f"{norm_smb(smb)}\t{path}\n")
PY

OPTS=$(cat /tmp/smb.opts)

# mount ทีละ share — ถ้าตัวใดล้มเหลว "ไม่" หยุดทั้งหมด (ให้ probe ตอน startup เตือนเอง
# เหมือนพฤติกรรม resilience ของ app: share หนึ่งล่มยังเสิร์ฟ mount อื่นได้)
while IFS="$(printf '\t')" read -r SMB DEST; do
    [ -z "$SMB" ] && continue
    mkdir -p "$DEST"
    echo "[entrypoint] mounting $SMB -> $DEST"
    if ! mount -t cifs "$SMB" "$DEST" \
        -o "credentials=/tmp/smb.cred,${OPTS},ro,uid=0,gid=0,iocharset=utf8"; then
        echo "[entrypoint] WARN: mount $SMB ล้มเหลว (จะปล่อยให้ app probe เตือนตอน startup)"
    fi
done < /tmp/mounts.tsv

rm -f /tmp/smb.cred /tmp/smb.opts /tmp/mounts.tsv

# รัน launcher เป็น root ตัวเดียว (ไม่ drop เป็น appuser) เพื่อให้เขียน bind mount (config seal /
# cifs.key / logs) ได้สม่ำเสมอทั้งบน Docker Desktop (mac) และ Linux — host dir เป็นของ user host
exec python -u server_launcher.py
