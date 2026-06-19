# HTTP-Image-Server — รายงานตรวจสอบโค้ด & บันทึกการพัฒนา

อัปเดตล่าสุด: 2026-06-19
เวอร์ชันปัจจุบัน: **2.6**
ไฟล์หลัก: `HTTP_Image_Server.py`, `LogLibrary.py`, `HTTP-Image-Server_config.json`
ไฟล์ deploy (Linux/Docker): `config.docker.json` (**config ที่เดียว**), `entrypoint.sh`, `secret_util.py` (เข้ารหัส password), `server_launcher.py`, `Dockerfile`, `docker-compose.yml`, `nginx.conf`, `requirements.txt`, `.dockerignore`

> เซิร์ฟเวอร์รูปภาพ: รับ path สัมพัทธ์ผ่าน `/image/{path}` แล้วค้นหาในหลาย mount
> (local disk / network share UNC) ตามลำดับความสำคัญ แล้วส่งไฟล์แรกที่เจอกลับ
> deploy เป็น **PyInstaller .exe บน Windows**, mount เป็น SMB share เช่น `\\172.30.54.1\image\`

---

## สารบัญ
1. [สถาปัตยกรรมปัจจุบัน (v2.4)](#1-สถาปัตยกรรมปัจจุบัน-v24)
2. [บั๊กที่ตรวจพบและแก้ไขแล้ว](#2-บั๊กที่ตรวจพบและแก้ไขแล้ว)
3. [เส้นทางเรื่อง Performance / Multi-worker](#3-เส้นทางเรื่อง-performance--multi-worker)
4. [ผล Benchmark](#4-ผล-benchmark)
5. [คู่มือ Config](#5-คู่มือ-config)
6. [วิธี deploy & การแก้ปัญหาที่พบบ่อย](#6-วิธี-deploy--การแก้ปัญหาที่พบบ่อย)
7. [ประวัติเวอร์ชัน](#7-ประวัติเวอร์ชัน)
8. [Deploy บน Ubuntu + Docker (เร็วที่สุด, ต้นทาง Windows SMB)](#8-deploy-บน-ubuntu--docker-เร็วที่สุด-ต้นทาง-windows-smb)

---

## 1. สถาปัตยกรรมปัจจุบัน (v2.5)

- **รันแบบ single-process** (uvicorn 1 ตัว) — เป็นรูปแบบเดียวที่ .exe ตัวเดียวเสิร์ฟ port เดียว
  บน Windows ได้อย่างเสถียร (เหตุผลในหัวข้อ 3)
- **Async I/O + thread pool** — งานที่ block (เช็คไฟล์ + อ่านไฟล์ส่งกลับ) ทำใน thread pool
  ขนาดปรับได้ผ่าน `Max_Workers` (ตั้งใน `lifespan` ตอน startup) ทำให้รับงานพร้อมกันได้มาก
- **ค้นหลาย mount ใน "เธรดเดียวต่อคำขอ" แบบ early-exit ตามลำดับความสำคัญ** *(เปลี่ยนใน v2.5)* —
  เดิม (v2.4) ยิง 1 เธรด/mount ทำให้ที่ RPS สูงมี dispatch เยอะมาก (เช่น 4000 req/s × 3 mount =
  12,000 dispatch/s แย่ง GIL/thread pool). v2.5 ค้นในเธรดเดียววนตามลำดับ เจอ mount แรกก็หยุด —
  เคสปกติ (ไฟล์อยู่ mount แรก) เสีย `isfile` แค่ครั้งเดียว และไม่แตะ mount ช้าที่อยู่ท้าย ๆ
- **Logging ด้วย Loguru** — rotation ตามขนาด, retention ตามวัน, บีบอัด zip, ปลอดภัยกับหลาย process
  *(default prod = `INFO`; `DEBUG` เฉพาะตอนไล่ปัญหา เพราะ DEBUG = ~6 บรรทัด/req)*
- **กัน path traversal** 2 ชั้น (`_clean_relative_path` + เช็ค `commonpath` ใน `_find_in_mounts`)

```
Client ──HTTP(keep-alive)──> uvicorn (1 process, backlog=4096)
                   │  asyncio event loop
                   └─ get_image()  ──> asyncio.to_thread(_find_in_mounts)  [1 เธรด/คำขอ]
                                         DC ──> isfile? เจอ -> หยุด ────┐
                                         DR ──> (เช็คต่อเมื่อ DC miss)   ├─> FileResponse + Cache-Control
                                    Archive ──> (เช็คต่อเมื่อยัง miss) ─┘
```

---

## 2. บั๊กที่ตรวจพบและแก้ไขแล้ว

### 🔴 2.1 สร้าง/หมุนไฟล์ log ไม่ได้เมื่อถึง Limit  *(ปัญหาที่แจ้งตอนแรก)*
**อาการ:** พอ log โตถึง `Log_Size` (10 MB) loguru หมุนไฟล์ (rename → zip) ไม่ได้

**สาเหตุ:** ตอนรันหลาย process (multi-worker เวอร์ชันเก่า) ทุก process เปิดไฟล์ log
ตัวเดียวกันค้างไว้ พอ rotate บน Windows จะชน file lock (`WinError 32`) เพราะ process อื่น
ถือ handle อยู่ → rotation ล้มเหลว → log ตัน

**แก้:** `LogLibrary.py` เพิ่ม `enqueue=True` (เขียนผ่านคิว thread เดียว ปลอดภัยกับ multi-process)
และ `catch=True` (ถ้า sink error ไม่ทำโปรแกรมล่ม) — ทดสอบแล้วหมุน + zip ได้ 55 รอบติดไม่ crash

### 🔴 2.2 Server.spec ชี้ entry ผิดชื่อ
`.spec` เดิมชี้ `Server.py` แต่ไฟล์จริงคนละชื่อ → build ไม่ผ่าน *(ไฟล์ spec ถูกลบไปแล้ว
ถ้า build ใหม่ต้องชี้ entry เป็น `HTTP_Image_Server.py`)*

### 🟠 2.3 `Max_Workers` ไม่ถูกใช้งาน
เดิมประกาศใน config แต่ `uvicorn.run` ไม่เคยรับไปใช้ → **ปัจจุบันใช้คุมจำนวน I/O thread แล้ว**

### 🟡 2.4 InterceptHandler ไม่ตั้ง depth
log ของ uvicorn โชว์ฟังก์ชันเป็น `emit`/`callHandlers` ทุกบรรทัด → เพิ่มการไล่ stack หา caller จริง

### 🟡 2.5 Config path escape เกิน
`"C:\\\\DC"` (4 backslash) → แก้เป็น `"C:\\DC"` (2 backslash) ใน JSON

### 🟡 2.6 Port default ไม่ตรงกัน
fallback ในโค้ดเป็น 50000 แต่ค่าจริง 8080 → แก้ให้ตรงเป็น 8080

### 🟡 2.7 Load_Config ไม่กันไฟล์ config เสีย
ถ้า JSON พัง โปรแกรมล่มตั้งแต่ start → ครอบ try/except, fallback เป็น default + เตือน

### 🟡 2.8 ไม่มีตัวบอกว่า mount เข้าถึงได้ไหม
เพิ่ม `_probe_mounts()` ตอน startup — log `Mount [X] OK` หรือ `NOT accessible`
ช่วยดีบักเคส "เรียกไม่เจอ" จาก network share ที่ล่ม/ไม่มีสิทธิ์โดยตรง

### ⚪ 2.9 โค้ดส่วนเกิน
ลบ `global script_dir`, `default_config = default_config` ที่ไม่มีผล

### 🟠 2.10 ค้น mount เปลือง thread/คำขอ  *(แก้ใน v2.5)*
**อาการ:** เดิมแต่ละคำขอสร้าง `asyncio.create_task` + `to_thread(os.path.isfile)` 1 ตัวต่อ mount
→ N mount = N เธรด/คำขอ. ที่ 3000–4000 req/s × 3 mount = **9,000–12,000 dispatch/s** แย่ง GIL
และ thread pool หนัก; ทั้งยังเสีย `isfile` ทุก mount แม้เจอตั้งแต่ mount แรก (task ที่เหลือ `cancel()`
ไม่ได้จริง เพราะเธรดที่รัน `isfile` ค้างใน share ช้ายกเลิกกลางคันไม่ได้)

**แก้:** รวมเป็น `_find_in_mounts()` ที่วนทุก mount ตามลำดับใน **เธรดเดียวต่อคำขอ** แบบ early-exit
→ เคสปกติเสีย `isfile` ครั้งเดียว, ลด dispatch ลงเหลือ 1/คำขอ, และไม่แตะ mount ช้าที่อยู่ท้ายเมื่อเจอแล้ว

### 🟠 2.11 abspath ซ้ำทุก mount ทุกคำขอ  *(แก้ใน v2.5)*
เดิม `_check_one_mount` เรียก `os.path.abspath()` ทุกครั้ง ซึ่งภายในเรียก `os.getcwd()` (syscall).
v2.5 ใช้ `os.path.join(root_abs, rel)` ตรง ๆ (root ถูก abspath ตอน start แล้ว, rel ผ่านการ clean แล้ว)
→ ตัด syscall ส่วนเกินออกจากทุกคำขอ ขณะที่ยังคงเช็ค `commonpath` กัน traversal ไว้

### 🟡 2.12 Default log level เป็น DEBUG บน prod  *(แก้ใน v2.5)*
`DEBUG` log ~6 บรรทัด/คำขอ + `Log_Console=1` เขียน stdout ทุกบรรทัด = คอขวดชัดเจนที่ RPS สูง
→ เปลี่ยน default เป็น `INFO` (สรุป 1 บรรทัด/คำขอ); แนะนำตั้ง `Log_Console=0` เมื่อต้องการ 3000–4000 req/s

### 🟡 2.13 socket backlog/keep-alive ไม่ถูกตั้งสำหรับโหลดสูง  *(แก้ใน v2.5)*
เพิ่ม `backlog=4096` (คิว accept ของ socket — default 2048 อาจล้นช่วงพีค → client โดน refused)
และ `timeout_keep_alive=15` (คง connection ไว้ reuse เลี่ยง TCP handshake ทุกคำขอ — สำคัญมากที่ RPS สูง)

---

## 3. เส้นทางเรื่อง Performance / Multi-worker

หัวข้อนี้สำคัญเพราะมีการลองหลายวิธี — สรุปบทเรียนไว้กันพลาดซ้ำ

### 3.1 ทำไมไม่ใช้ uvicorn `workers=N`
uvicorn สร้าง worker ด้วยการ **re-launch ตัว .exe เอง** แล้ว import app ผ่าน import-string
ใน frozen build จะ import ไม่สำเร็จ → worker ตาย → supervisor restart วนไม่หยุด
(`Waiting for child process / Child process died`) = **crash loop**

### 3.2 ทำไมไม่ใช้ shared socket หลาย process (ลองใน v2.3 แล้วถอย)
ลอง bind socket เดียวแล้ว spawn worker ด้วย `multiprocessing` ให้ทุกตัว accept ร่วมกัน
- ✅ ใช้ได้บน **macOS / Linux**
- ❌ **ล้มเหลวบน Windows**: asyncio บน Windows ใช้ IOCP (Proactor) ซึ่ง register
  socket ที่ bind จาก process อื่นไม่ได้ → `OSError: [WinError 87] ... _register_with_iocp`
  ทุก worker accept ไม่ได้

> **ข้อเท็จจริง:** Windows ไม่มี `SO_REUSEPORT` → **หลาย process แชร์ port เดียวกันไม่ได้**
> การทำ multi-process บน Windows ต้องใช้ reverse proxy ข้างหน้าเท่านั้น

### 3.3 ข้อสรุป — single-process + thread pool (v2.4)
- รัน process เดียว เสถียรแน่นอนบน Windows, ไม่มี crash loop, banner ขึ้นครั้งเดียว
- คอขวด event loop อยู่ที่ 1 core แต่สำหรับงานเสิร์ฟไฟล์ยังทำได้ **หลายพัน req/s** (ดูหัวข้อ 4)
- `Max_Workers` = จำนวน I/O thread → เพิ่มได้ถ้า share ช้า/โหลดสูง

### 3.4 ถ้าต้องการ multi-core จริงบน Windows
รัน **.exe หลาย instance คนละ port** (8080, 8081, …) แล้ววาง **IIS ARR / nginx**
ข้างหน้าทำ load balancing — แต่ละ instance เป็น process อิสระ (ไม่แชร์ socket) จึงไม่ติดข้อจำกัด Windows

---

## 4. ผล Benchmark

ApacheBench `-c 200 -k`, ไฟล์ 50KB, localhost, เครื่อง 12-core:

| โหมด | req/s | failed | หมายเหตุ |
|------|------:|:------:|---------|
| single-process, Max_Workers=128 | **~2,800** | 0 | ✅ v2.4 (ที่ใช้จริง) |
| single-process, Max_Workers=32  | ~3,685 | 0 | ไฟล์ใน page cache |
| ~~8 process แชร์ socket~~ | ~~10,818~~ | 0 | ❌ ใช้บน Windows ไม่ได้ (WinError 87) |

> **สรุป:** เพดานจริงในระบบมักถูกจำกัดด้วย **bandwidth เครือข่าย** และ **latency ของ share** ไม่ใช่ตัว server
>
> | ขนาดรูปเฉลี่ย | 1000 req/s ต้องการ | 4000 req/s ต้องการ | NIC แนะนำ |
> |:-:|:-:|:-:|:-:|
> | 50 KB  | ~400 Mbps/ขา | ~1.6 Gbps/ขา | 10 Gbps |
> | 200 KB | ~1.6 Gbps/ขา | ~6.4 Gbps/ขา | 25 Gbps |

### 4.1 ❓ รองรับ 3000–4000 req/s ได้ไหม?

**สรุปสั้น:** ได้ — แต่ต้องเข้าใจเงื่อนไขและเลือกโหมด deploy ให้ถูก

| สถานการณ์ | 3000–4000 req/s? | หมายเหตุ |
|---|:-:|---|
| ไฟล์อยู่ใน **local disk / page cache**, client ใช้ keep-alive, log = INFO/console off, เครื่อง ≥8 core | ⚠️ **เฉียด ๆ** | single-process v2.5 ทำได้ ~3,500–4,500 req/s ตามผล tune (handler เร็ว ~0.1–0.2 ms/คำขอ). แต่ event loop อยู่ 1 core จึง **ไม่มี headroom** — พีคจริงเสี่ยงตก |
| ไฟล์อยู่บน **network share (SMB/UNC) ที่มี latency** | ❌ single-process ไม่พอ | แต่ละ `isfile`/อ่านไฟล์รอ network เป็น ms → ต้องใช้ thread เยอะ และ 1 core ประมวลผลไม่ทัน |
| **หลาย instance (.exe หลายตัว คนละ port) + reverse proxy** | ✅ **แนะนำ** | วิธีที่ "การันตี" 3000–4000 req/s บน Windows ได้จริง (ดูข้อ 3.4) |

**คำแนะนำเชิงปฏิบัติเพื่อให้ถึง 3000–4000 req/s อย่างมั่นใจ:**

1. **รัน 2–3 instance คนละ port (8080/8081/8082) แล้ววาง IIS ARR หรือ nginx ข้างหน้า** —
   แต่ละ instance รับ ~1,500–2,500 req/s รวมกันเกิน 4000 พร้อม headroom และทนเครื่องพีค.
   นี่คือทางเดียวที่ใช้ "หลาย core" จริงบน Windows (single-process ติด 1 core, ดูข้อ 3).
2. **ปิด console log + ใช้ `log_Level=INFO`** (`Log_Console=0`) — เขียน stdout ทุกคำขอเป็นคอขวด.
3. **เปิด HTTP keep-alive ฝั่ง client/proxy** — เลี่ยง TCP handshake ทุกคำขอ (v2.5 ตั้ง `timeout_keep_alive=15`).
4. **ตั้ง `Max_Workers` ให้พอกับ latency ของ share** — share ช้า → 128–256 (แต่ละ thread รอ network พร้อมกัน).
5. **เผื่อ bandwidth NIC** ตามตารางด้านบน — 4000 req/s × 50 KB ≈ 1.6 Gbps/ขา (ขาเข้า share + ขาออก client).

> ⚠️ **ข้อจำกัดที่แก้ในโค้ดไม่ได้:** single-process = event loop 1 core. การปรับ thread (v2.5) ช่วยลด
> overhead ต่อคำขอจนเฉียด 4000 ได้ในเคส cache แต่ **ไม่ทำให้ 1 core กลายเป็นหลาย core** — ปริมาณ
> งานระดับนี้ที่เสถียรต้อง scale แนวนอน (หลาย instance + proxy) ตามข้อ 1

---

## 5. คู่มือ Config

`HTTP-Image-Server_config.json`:

| key | ความหมาย | ค่าแนะนำ |
|-----|----------|---------|
| `Mapdrive` | list ของ mount `{name, path}` ค้นตามลำดับ (บนสุด = priority สูงสุด) | UNC ใช้ `"\\\\172.30.54.1\\image\\"` |
| `Port_Server` | port ที่ฟัง | 8080 / 50000 |
| `Max_Workers` | จำนวน I/O thread (process เดียว) | 64 ปกติ / **128–256** ถ้า share ช้า |
| `Cache_Max_Age` | อายุ Cache-Control ของรูป (วินาที) | 3600 / 86400 |
| `log_Level` | DEBUG / INFO / WARNING / ERROR | prod ใช้ `INFO` (default v2.5) — `DEBUG` เฉพาะไล่ปัญหา |
| `Log_Console` | 1 = log ออกจอด้วย | 1 ปกติ / **0** เมื่อต้องการ 3000–4000 req/s |
| `log_Backup` | เก็บ log ย้อนหลังกี่วัน | 90 |
| `Log_Size` | ขนาดไฟล์ก่อนหมุน | "10 MB" |

**Logging แยกตาม level:**
- `DEBUG` — เห็นทุกขั้น: รับ request, normalize path, ผลเช็คทุก mount, full path, mount paths ตอน start
- `INFO` — สรุป 1 บรรทัด/request (`200 OK | mount=DC | rel=... | 0.9 ms`) + สถานะ mount ตอน start
- `WARNING` — 404 / path ไม่ถูกต้อง (400) / mount เข้าไม่ถึง
- `ERROR` — error 500

---

## 6. วิธี deploy & การแก้ปัญหาที่พบบ่อย

### Build .exe (PyInstaller)
entry คือ `HTTP_Image_Server.py` (ชื่อต้องเป็น underscore เพื่อให้ import ได้) เช่น:
```
pyinstaller --onefile --console HTTP_Image_Server.py
```
วาง `HTTP-Image-Server_config.json` ไว้ข้าง .exe (โปรแกรมอ่าน config/เขียน logs ข้างไฟล์ที่รัน)

### "เรียกไม่เจอ" (404) — ไล่เช็คตามนี้
1. **ดู log ตอน start** — ถ้าเห็น `Mount [X] NOT accessible` แปลว่า server เข้า share นั้นไม่ได้
   (network ล่ม / path ผิด / account ที่รันไม่มีสิทธิ์เข้า SMB share)
2. **URL ต้องเป็น path สัมพัทธ์กับ root ของ share** — ไฟล์จริง `\\172.30.54.1\image\a\b.jpg`
   เรียกด้วย `GET http://server:port/image/a/b.jpg` (ไม่ใส่ `\\172...\image` ใน URL)
   *(slash นำหน้า/slash คู่ `/image//a/b.jpg` ใช้ได้ ระบบตัดให้เอง)*
3. **เช็คว่า server รันเสถียร** — ถ้าเป็น build เก่าที่มี multi-worker จะ crash loop ให้ build ใหม่ด้วย v2.4

### log ขึ้น banner หลายครั้ง / "เหมือนรันหลายโปรแกรม"
เป็นอาการของ build เก่า (multi-worker) → v2.4 เป็น single-process banner ขึ้นครั้งเดียว **ต้อง build ใหม่**

---

## 7. ประวัติเวอร์ชัน

| เวอร์ชัน | สาระสำคัญ |
|:--:|---|
| 1.7 | เวอร์ชันเริ่มต้น + รายงานตรวจสอบ |
| 1.8 | แก้ log rotation ตอนถึง Limit (`enqueue=True`, `catch=True`), กัน config เสีย, fix port/escape |
| 1.9 | Cache-Control, early-exit, logging แยก level, InterceptHandler depth *(เปลี่ยนชื่อไฟล์เป็น underscore)* |
| 2.0 | startup mount probe, แก้ multi-worker crash-loop เมื่อ frozen |
| 2.1 | ขยาย I/O thread pool (process เดียว) |
| 2.2 | `Max_Workers` = จำนวน thread, ตัด multi-worker ออก |
| 2.3 | ลอง multi-process แชร์ socket *(ใช้ได้ Linux/Mac แต่ Windows ไม่ได้ — ถอยใน 2.4)* |
| 2.4 | กลับเป็น single-process (Windows แชร์ socket ไม่ได้), `Max_Workers` = I/O thread |
| 2.5 | ค้น mount เป็น 1 เธรด/คำขอ (early-exit, ลด dispatch จาก N→1), ตัด abspath ซ้ำ, default log = INFO, ตั้ง `backlog=4096` + `timeout_keep_alive=15`, ตอบคำถามรองรับ 3000–4000 req/s |
| **2.6** | **โหมด Linux/Docker** (เน้นใช้งานหลัก): `server_launcher.py` (fork หลาย worker แชร์ socket = multi-core) + uvloop + httptools, **log "ไฟล์เดียว" ผ่าน queue (writer ใน parent) แม้หลาย worker**, **config ที่เดียว** (`config.docker.json`) + `entrypoint.sh` mount SMB เอง (แปลง UNC `\\..\` → `//..` อัตโนมัติ) **รองรับ AD domain**, **เข้ารหัส password encrypt-on-first-run** (`secret_util.py`, Fernet), รันเป็น root + `cap SYS_ADMIN`/`DAC_READ_SEARCH`, nginx cache, `immutable` Cache-Control — *ทดสอบจริงกับ Windows SMB share ผ่านครบ (mount + ดึง .jpg + cache HIT + seal)* |

### ✅ จุดที่ดีอยู่แล้วในโค้ด
- กัน path traversal ครบ 2 ชั้น (`commonpath`)
- ใช้ `asyncio.to_thread` ไม่บล็อก event loop
- ค้น mount แบบ early-exit เคารพลำดับความสำคัญ (v2.5 รวมเป็นเธรดเดียว/คำขอ — ดูข้อ 2.10)
- redirect logging ของ uvicorn เข้า Loguru สำเร็จ

---

## 8. Deploy บน Ubuntu + Docker (เร็วที่สุด, ต้นทาง Windows SMB)

โหมดนี้ "ไม่ freeze" จึงปลดล็อกสิ่งที่ .exe บน Windows ทำไม่ได้: **uvloop (event loop เร็วขึ้น 2–4×),
multi-core workers, httptools**. แต่จุดสำคัญที่สุดของเคสนี้คือ **ต้นทางไฟล์เป็น Windows SMB share** →
คอขวดจะย้ายจาก server ไปอยู่ที่ **latency ของ SMB/CIFS** ดังนั้นกลยุทธ์ "เร็วที่สุด" คือ
**ทำให้คำขอส่วนใหญ่ไม่แตะ SMB เลย ด้วย cache** (รูป immutable → cache ยาวได้)

> ✅ **Config ที่เดียว:** แก้ทุกอย่างใน **`config.docker.json` ไฟล์เดียว** (share, บัญชี AD, path,
> workers, cache, log) — `entrypoint.sh` อ่าน config นี้แล้ว **mount CIFS ให้เอง** ตอนบูต ไม่ต้อง
> แก้ `docker-compose.yml` อีก. แลกกับการที่ container ต้องได้ `cap_add: SYS_ADMIN` + `DAC_READ_SEARCH`
> (เพราะ mount เองในคอนเทนเนอร์) และรันเป็น **root** (ให้เขียน bind mount ได้ทั้ง mac/Linux)
>
> **ทดสอบจริงกับ Windows SMB share ผ่านครบ:** mount ทั้ง 3 share ติด → ดึง `.jpg` จริงผ่าน server
> ได้ 200 (mount=DC, 3.6 ms) → ยิงซ้ำ `X-Cache-Status: HIT` จาก nginx RAM → password ถูก seal เป็น
> `enc:` → log ไฟล์เดียวที่ `./logs` ✔

### 8.1 สถาปัตยกรรม

```
client ──HTTP keep-alive──> nginx (multi-core, proxy_cache บน tmpfs/RAM)
   │  cache HIT  -> ตอบจาก RAM ~0.1 ms  ไม่แตะ app, ไม่แตะ SMB         ← รับโหลด 3000-4000 req/s ที่นี่
   │  cache MISS -> server_launcher.py: parent + N workers (uvloop, แชร์ socket ผ่าน fork; รันเป็น root)
   │                  └─ _find_in_mounts() -> CIFS mount -> //172.30.54.x/image (DC/DR/BK-NAS)
   └─ คำขอแรกของแต่ละไฟล์เท่านั้นที่จ่าย latency SMB; ครั้งถัดไปมาจาก cache
```

> **รันเป็น root ในคอนเทนเนอร์** (ไม่ drop เป็น appuser): เพื่อให้ entrypoint mount CIFS และเขียน
> bind mount (seal config / `cifs.key` / logs) ได้สม่ำเสมอทั้ง Docker Desktop (mac) และ Linux
> โดยไม่ต้องชน uid-mapping. host dir เป็นของ user host (ไม่ต้อง chown 1000)

**App เป็นหลาย worker ได้ และยัง log "ไฟล์เดียว" ผ่าน queue** (`server_launcher.py`):
- `Workers` ใน `config.docker.json` = จำนวน process (multi-core). worker ทุกตัว **fork** จาก parent
  จึง **แชร์ listening socket เดียว** (Linux kernel load-balance accept ให้ — ใช้หลาย core จริง
  โดยไม่ติดข้อจำกัด Windows เพราะนี่คือ Linux)
- parent ตั้ง Loguru `enqueue=True` ไว้ก่อน fork → **queue + writer thread อยู่ที่ parent เดียว**.
  worker แค่ `put` ข้อความลง queue ที่ inherit มา, **parent เป็นคนเดียวที่เขียนไฟล์** →
  log รวมเป็น **ไฟล์เดียว, rotation/zip ไม่ชนกัน, ลำดับถูกต้อง** (เลี่ยงบั๊กเดิมข้อ 2.1 ที่หลาย
  process เขียน/หมุนไฟล์เดียวกันแล้วชน)
- ถ้าทั้งระบบมี nginx cache หน้าอยู่แล้ว app จะเห็นแค่ cache miss (I/O-bound รอ SMB) — ตั้ง `Workers`
  เท่าจำนวน vCPU ก็พอ ไม่ต้องเยอะ

### 8.2 Logging (queue + ไฟล์เดียว + map ออก host)
- `enqueue=True` ใน Loguru = ทุกข้อความผ่าน **queue** แล้วเขียนด้วย thread เดียว (ไม่บล็อก request,
  ลำดับถูกต้อง) + `catch=True` กัน sink error ทำโปรแกรมล่ม
- **หลาย worker → ไฟล์เดียว:** `server_launcher.py` fork worker หลังจาก parent ตั้ง sink แล้ว →
  worker แชร์ queue เดียวกัน, parent เป็น writer เดียว (พิสูจน์แล้ว: 2 worker เสิร์ฟพร้อมกัน
  log ลงไฟล์เดียวครบทุกบรรทัด)
- `config.docker.json`: `Log_File=1`, `Log_Console=0` → เขียน **"ที่เดียว"** คือไฟล์ `/app/logs/...log`
- `docker-compose.yml` map ออก host:
  - `./config.docker.json -> /app/HTTP-Image-Server_config.json` (แก้ config จาก host ได้)
  - `./logs -> /app/logs` (อ่าน log ได้จาก host ที่โฟลเดอร์ `./logs`)
- ใหม่ใน v2.6: เพิ่ม `Log_File` (0/1) ใน config — ปิดไฟล์ log ได้ถ้าอยากให้ Docker เก็บ stdout แทน

### 8.3 SMB mount จาก config ไฟล์เดียว (รองรับ AD domain)
`entrypoint.sh` อ่าน `config.docker.json` แล้ว `mount -t cifs` ให้เองตอนบูต — ไม่ใช้ docker volume แล้ว
ทุก mount ที่มี key `"smb"` จะถูก mount ไปยัง `"path"` ที่ระบุ:

```json
"Mapdrive": [
    { "name": "DC", "path": "/mnt/DC", "smb": "\\\\172.30.54.1\\image\\" }
],
"Smb_Domain":  "YOURDOMAIN",
"Smb_User":    "readonly_user",
"Smb_Pass":    "********",
"Smb_Options": "vers=3.1.1,sec=ntlmssp,cache=loose,actimeo=600,rsize=4194304,wsize=4194304"
```

| ค่า / option | เหตุผล |
|---|---|
| `smb` รูปแบบ UNC | ใส่แบบ Windows ได้เลย (`\\172.30.54.1\image\`) — entrypoint **แปลงเป็น `//172.30.54.1/image` ให้อัตโนมัติ** |
| `Smb_Domain` + `sec=ntlmssp` | **บัญชีเป็น AD/domain** → ต้องระบุ domain (เขียนลง credentials file) + บังคับ NTLMSSP |
| `Smb_User` / `Smb_Pass` | บัญชีที่อ่าน share ได้ — entrypoint เขียนลง credentials file (mode 600) ไม่โผล่ใน `ps`/`mount` |
| `vers=3.1.1` | เปิด SMB3 → ได้ multichannel (หลาย TCP stream เพิ่ม bandwidth) |
| `cache=loose` + `actimeo=600` | รูป immutable → cache attribute ได้นาน ลด round-trip ของ `isfile` มหาศาล |
| `rsize/wsize=4M` | อ่านก้อนใหญ่ ลดจำนวน round-trip ต่อไฟล์ |
| `ro,uid=0,gid=0` | entrypoint เติมให้อัตโนมัติ: read-only + เป็นของ root (ตรงกับ process ที่รันเป็น root) |

> - `Mapdrive.path` = container path (`/mnt/DC`...) ไม่ใช่ `C:\` — ตัว CIFS เชื่อมให้
> - รองรับ subdir ใน share ได้ เช่น `\\NAS\ShareName\image\` → `//NAS/ShareName/image`
> - mount ใดล้มเหลว **ไม่ทำให้ทั้ง container ตาย** — `_probe_mounts()` ตอน startup จะ log `NOT accessible` เตือน
> - mount entry ที่ไม่มี `"smb"` = ไม่ mount (ใช้ path ตามมีตามเกิด เช่น bind mount/local)
> - ต้องตั้ง `cap_add: [SYS_ADMIN, DAC_READ_SEARCH]` ใน compose — ขาด `DAC_READ_SEARCH` จะเจอ
>   `mount error: Unable to apply new capability set` (ตั้งไว้ให้แล้ว)

### 8.4 nginx cache (`nginx.conf`)
- เข้าผ่าน `http://<host>/image/<path>` (compose map `80:80`; ถ้าพอร์ต 80 ชน เปลี่ยนซ้ายเป็น `8080:80`)
- `proxy_cache_path .../var/cache/nginx` บน **tmpfs (RAM)** ใน compose → cache hit ตอบจาก RAM
- `proxy_cache_valid 200 365d` (รูป immutable), `404 10s` (กันยิงซ้ำถี่)
- `proxy_cache_lock on` → cold cache: คำขอแรกของไฟล์ไป app, ที่เหลือรอ cache (กัน stampede ไป SMB)
- `keepalive` ทั้งฝั่ง client และ upstream → เลี่ยง TCP handshake ทุกคำขอ
- ส่ง header `X-Cache-Status` (HIT/MISS/LOCK) ไว้ดีบัก hit rate

### 8.5 ขั้นตอนรัน
```bash
# 1) แก้ config ไฟล์เดียว: ใส่ Smb_Domain / Smb_User / Smb_Pass / smb ของแต่ละ share + Workers
nano config.docker.json
# 2) เตรียมโฟลเดอร์ log + secret ให้เป็นของ user host (รันเป็น root จึงไม่ต้อง chown 1000)
mkdir -p logs secret
sudo chown -R "$(id -u):$(id -g)" logs secret      # เผื่อเคยถูก chown เป็น uid อื่นไว้
# 3) build + run (app จะ mount SMB เองจาก config ตอนบูต)
docker compose up -d --build
# 4) ตรวจสอบ
docker compose ps                                   # ทั้ง app + nginx ต้อง Up (ไม่ Restarting)
docker compose logs app | grep -E "mounting|WARN"   # mount ติดครบ? (ไม่ควรมี WARN)
ls -la logs secret                                  # ต้องเห็น ...2.6.log และ cifs.key
curl -sD - -o /dev/null http://<host>/image/<path>/<file>.jpg | grep -i "HTTP/\|X-Cache"
#   ครั้งแรก X-Cache-Status: MISS, ยิงซ้ำ -> HIT
```

> ⚠️ **secret:** หลังรันครั้งแรก `Smb_Pass` ใน config จะกลายเป็น `enc:` และมีคีย์ที่ `./secret/cifs.key`
> — `secret/` และ `config.docker.json` อยู่ใน `.gitignore` แล้ว **อย่าลบ `./secret/` / อย่า commit คีย์**

### 8.5.1 ปัญหาที่เจอจริงตอน deploy & วิธีแก้
| อาการ | สาเหตุ | แก้ |
|---|---|---|
| `failed to xattr logs: permission denied` ตอน build | `logs/`/`secret/` ถูก `chown 1000` แต่ host user เป็น uid อื่น (เช่น mac=501) อ่าน context ไม่ได้ | `sudo chown -R $(id -u):$(id -g) logs secret` + มี `.dockerignore` กัน `logs/ secret/` ออกจาก context |
| `Bind for 0.0.0.0:80 failed: port is already allocated` | พอร์ต 80 ถูก container/โปรเซสอื่นใช้ | compose ใช้ `8080:80` แล้ว (เปลี่ยนเลขซ้ายได้ถ้ายังชน) |
| `PermissionError: /app/secret/cifs.key` (app crash loop) | `secret/` เป็นของ uid ที่ root ในคอนเทนเนอร์เขียนไม่ได้ (mac virtiofs) | ให้ `secret/` เป็นของ user host (รันเป็น root แล้วเขียน 501-owned dir ได้) |
| `mount error: Unable to apply new capability set` | ขาด cap `DAC_READ_SEARCH` ที่ `mount.cifs` ต้องใช้ | compose ใส่ `cap_add: [SYS_ADMIN, DAC_READ_SEARCH]` แล้ว |
| `Mount [X] NOT accessible` | เน็ตไม่ถึง / บัญชี AD ไม่มีสิทธิ์ / `vers` ไม่ตรง SMB server | เช็ค `nc -z <ip> 445`, สิทธิ์บน share, ลอง `vers=3.0`/`2.1` ถ้า server เก่า |

### 8.6 จะเร็วขึ้นแค่ไหน?
| ชั้น | ผล |
|---|---|
| nginx cache HIT | ตอบจาก RAM ~0.1 ms → **เกิน 4000 req/s สบาย** (ไม่แตะ app/SMB) |
| app + uvloop (cache miss) | event loop เร็วขึ้น 2–4× เทียบ asyncio ปกติ; miss เป็น I/O-bound รอ SMB |
| CIFS tuning | ลด latency/round-trip ของ miss ที่ต้องวิ่ง SMB |

> **สรุป:** เป้า 3000–4000 req/s ทำได้จริงในโหมดนี้ เพราะภาระหลักไปตกที่ nginx cache (RAM, multi-core)
> ส่วน SMB share จะเห็นเฉพาะ "ไฟล์ที่ยังไม่ถูก cache" เท่านั้น — ยิ่ง hit rate สูง SMB ยิ่งสบาย

### 8.7 เข้ารหัสรหัสผ่าน SMB (encrypt-on-first-run)
รหัสผ่าน AD ใน config ไม่ต้องเก็บเป็น plaintext ตลอดไป — `secret_util.py` จัดการให้อัตโนมัติ:

```
รันครั้งแรก:  "Smb_Pass": "<plaintext>"    ──(entrypoint)──>  "Smb_Pass": "enc:gAAAAAB..."
รันครั้งถัดไป: เห็น prefix enc: -> ถอดรหัสกลับมาใช้ mount (เงียบ ๆ ไม่แก้ไฟล์)
```

- **คีย์:** Fernet (AES-128-CBC + HMAC) เก็บที่ `/app/secret/cifs.key` (สร้างครั้งแรก, mode 600)
  → map ออก host `./secret` ใน compose เพื่อให้ **persist** (รีสตาร์ตแล้วถอดรหัสได้)
- **config ต้อง mount แบบ rw** (ไม่ใช่ `:ro`) เพราะครั้งแรกต้องเขียน `enc:` กลับ — ตั้งไว้ใน compose แล้ว
- **ห้ามทำหาย/อย่า commit:** `./secret/cifs.key` (และ `config.docker.json` ที่มี enc:) อยู่ใน `.gitignore`
  ถ้าคีย์หายขณะ config เป็น `enc:` → ถอดรหัสไม่ได้ โปรแกรมจะ **ฟ้อง error ชัดเจน** พร้อมวิธีแก้
  (ใส่รหัส plaintext กลับลง config แล้วรันใหม่เพื่อ re-seal ด้วยคีย์ปัจจุบัน)
- **threat model:** กันรหัส plaintext ค้างในไฟล์ config (ที่อาจถูกแชร์/commit/เปิดดู).
  ไม่ได้กันผู้ที่ได้ทั้ง config + keyfile พร้อมกัน → เก็บ keyfile ให้ดี (สิทธิ์ 600)
- **เปลี่ยนรหัสผ่านภายหลัง:** แก้ `Smb_Pass` ใน config เป็น plaintext ใหม่ (ทับ `enc:` เดิม) แล้วรันใหม่
  → ระบบจะ seal ให้เป็น `enc:` รอบใหม่อัตโนมัติ

*(ทดสอบจริงครบ: seal ครั้งแรก → restart ถอดรหัสได้รหัสเดิม → กรณีคีย์หายฟ้อง error ✔)*
