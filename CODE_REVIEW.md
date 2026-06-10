# HTTP-Image-Server — รายงานตรวจสอบโค้ด & บันทึกการพัฒนา

อัปเดตล่าสุด: 2026-06-10
เวอร์ชันปัจจุบัน: **2.4**
ไฟล์หลัก: `HTTP_Image_Server.py`, `LogLibrary.py`, `HTTP-Image-Server_config.json`

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

---

## 1. สถาปัตยกรรมปัจจุบัน (v2.4)

- **รันแบบ single-process** (uvicorn 1 ตัว) — เป็นรูปแบบเดียวที่ .exe ตัวเดียวเสิร์ฟ port เดียว
  บน Windows ได้อย่างเสถียร (เหตุผลในหัวข้อ 3)
- **Async I/O + thread pool** — งานที่ block (เช็คไฟล์ / อ่านไฟล์ส่งกลับ) ทำใน thread pool
  ขนาดปรับได้ผ่าน `Max_Workers` (ตั้งใน `lifespan` ตอน startup) ทำให้รับงานพร้อมกันได้มาก
- **ค้นหลาย mount พร้อมกัน แต่ตอบตามลำดับความสำคัญ** — เจอ mount แรกคืนทันที ไม่รอ mount ช้า
- **Logging ด้วย Loguru** — rotation ตามขนาด, retention ตามวัน, บีบอัด zip, ปลอดภัยกับหลาย process
- **กัน path traversal** 2 ชั้น (`_clean_relative_path` + เช็ค `commonpath` ใน `_check_one_mount`)

```
Client ──HTTP──> uvicorn (1 process)
                   │  asyncio event loop
                   └─ get_image()  ──> ค้นทุก mount พร้อมกัน (thread pool)
                                         DC ─┐
                                         DR ─┼─> เจอตัวแรกตามลำดับ -> FileResponse + Cache-Control
                                    Archive ─┘
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

> **สรุป:** เป้าหมาย 1000 req/s — single-process ทำได้สบาย (เกิน 2.8 เท่า)
> เพดานจริงในระบบมักถูกจำกัดด้วย **bandwidth เครือข่าย** และ **latency ของ share** ไม่ใช่ตัว server
>
> | ขนาดรูปเฉลี่ย | 1000 req/s ต้องการ | NIC แนะนำ |
> |:-:|:-:|:-:|
> | 50 KB  | ~400 Mbps/ขา | 1 Gbps |
> | 200 KB | ~1.6 Gbps/ขา | 10 Gbps |

---

## 5. คู่มือ Config

`HTTP-Image-Server_config.json`:

| key | ความหมาย | ค่าแนะนำ |
|-----|----------|---------|
| `Mapdrive` | list ของ mount `{name, path}` ค้นตามลำดับ (บนสุด = priority สูงสุด) | UNC ใช้ `"\\\\172.30.54.1\\image\\"` |
| `Port_Server` | port ที่ฟัง | 8080 / 50000 |
| `Max_Workers` | จำนวน I/O thread (process เดียว) | 64 ปกติ / **128–256** ถ้า share ช้า |
| `Cache_Max_Age` | อายุ Cache-Control ของรูป (วินาที) | 3600 / 86400 |
| `log_Level` | DEBUG / INFO / WARNING / ERROR | prod ใช้ `INFO` |
| `Log_Console` | 1 = log ออกจอด้วย | 1 |
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
| **2.4** | **กลับเป็น single-process** (Windows แชร์ socket ไม่ได้), `Max_Workers` = I/O thread |

### ✅ จุดที่ดีอยู่แล้วในโค้ด
- กัน path traversal ครบ 2 ชั้น (`commonpath`)
- ใช้ `asyncio.to_thread` ไม่บล็อก event loop
- ค้นหลาย mount พร้อมกันแบบ early-exit เคารพลำดับความสำคัญ
- redirect logging ของ uvicorn เข้า Loguru สำเร็จ
