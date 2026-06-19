"""เข้ารหัสรหัสผ่าน SMB ใน config แบบ 'encrypt-on-first-run'.

flow:
  - ครั้งแรก: config มี `Smb_Pass` เป็น plaintext (รหัสจริง) -> โปรแกรมเข้ารหัสแล้วเขียนกลับ
    เป็น `"Smb_Pass": "enc:<token>"` (in place) -> plaintext หายจากไฟล์
  - ครั้งถัดไป: เห็น prefix `enc:` -> ถอดรหัสกลับมาใช้ตอน mount

คีย์:
  - เก็บแยกไฟล์ (ไม่อยู่ใน config) ที่ path ใน env `IMG_KEY_FILE` หรือ default `/app/secret/cifs.key`
  - สร้างอัตโนมัติครั้งแรก (mode 600). ไฟล์นี้ต้อง persist (mount ออก host) — ถ้าหายจะถอดรหัสไม่ได้
  - ใช้ Fernet (AES-128-CBC + HMAC-SHA256) จากไลบรารี cryptography

threat model (พูดตรง ๆ): ป้องกัน "plaintext ติดอยู่ในไฟล์ config" ที่อาจถูกแชร์/commit/เปิดดู
ไม่ได้ป้องกันผู้ที่ได้ทั้ง config (ciphertext) + keyfile พร้อมกัน -> เก็บ keyfile ให้ดี (สิทธิ์ 600, อย่า commit)
"""
import json
import os

from cryptography.fernet import Fernet, InvalidToken

ENC_PREFIX = "enc:"
DEFAULT_KEY_PATH = os.environ.get("IMG_KEY_FILE", "/app/secret/cifs.key")


def _load_or_create_key(key_path=DEFAULT_KEY_PATH):
    d = os.path.dirname(key_path)
    if d:
        os.makedirs(d, exist_ok=True)
    if os.path.exists(key_path):
        with open(key_path, "rb") as f:
            return f.read().strip()
    key = Fernet.generate_key()
    # เขียนคีย์แบบ 600 (เจ้าของอ่านได้คนเดียว)
    fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(key)
    return key


def _fernet(key_path=DEFAULT_KEY_PATH):
    return Fernet(_load_or_create_key(key_path))


def encrypt(plain, key_path=DEFAULT_KEY_PATH):
    token = _fernet(key_path).encrypt(plain.encode("utf-8")).decode("ascii")
    return ENC_PREFIX + token


def decrypt(value, key_path=DEFAULT_KEY_PATH):
    token = value[len(ENC_PREFIX):]
    return _fernet(key_path).decrypt(token.encode("ascii")).decode("utf-8")


def is_encrypted(value):
    return isinstance(value, str) and value.startswith(ENC_PREFIX)


def get_password_and_seal(config_path, field="Smb_Pass", key_path=DEFAULT_KEY_PATH):
    """คืน (plaintext_password, config_dict) สำหรับใช้ mount.

    ถ้า field ยังเป็น plaintext -> เข้ารหัสแล้วเขียนกลับ config (in place, ไม่ใช้ rename
    เพราะ config มักเป็น bind-mounted file ที่ rename ทับไม่ได้).
    ถ้าเป็น enc: อยู่แล้ว -> ถอดรหัสกลับมา
    """
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    val = cfg.get(field, "")

    if is_encrypted(val):
        try:
            return decrypt(val, key_path), cfg
        except InvalidToken:
            raise SystemExit(
                f"[secret] ถอดรหัส {field} ไม่ได้ — คีย์ ({key_path}) หาย/ไม่ตรง. "
                f"วิธีแก้: ใส่รหัสผ่าน plaintext กลับลง {config_path} ใหม่ แล้วรันอีกครั้งเพื่อ re-seal")

    # plaintext -> เข้ารหัสแล้วเขียนทับในที่ (ครั้งแรก)
    plain = val
    if plain:
        cfg[field] = encrypt(plain, key_path)
        tmp = json.dumps(cfg, indent=4, ensure_ascii=False) + "\n"
        with open(config_path, "w", encoding="utf-8") as f:
            f.write(tmp)
        print(f"[secret] sealed {field} in {config_path} (plaintext -> enc:)")
    return plain, cfg


if __name__ == "__main__":
    # ใช้เทสต์/seal ด้วยมือ: python secret_util.py <config_path>
    import sys
    pw, _ = get_password_and_seal(sys.argv[1])
    print("[secret] password length:", len(pw))
