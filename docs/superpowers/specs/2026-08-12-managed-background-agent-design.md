# Managed Background Agent Foundation — Design Specification

วันที่: 2026-08-12
สถานะ: Draft for written-spec review
ขอบเขต: Phase 1 — agent lifecycle foundation

## 1. เป้าหมาย

สร้างโครงสร้างพื้นฐานของ Managed Background Agent สำหรับ PhantomLink ที่ทำงานเบื้องหลังบน Windows เชื่อมต่อกลับไปยัง controller ได้อย่างเสถียร และเป็นฐานให้ Unified Command Center ในเฟสถัดไป

Phase 1 ต้องส่งมอบ:

- entry point แยกจาก legacy client
- lifecycle state machine ที่สังเกตและทดสอบได้
- reconnect/retry พร้อม exponential backoff และ jitter
- application heartbeat และ read deadline
- clean shutdown ที่ยกเลิก connect/read/backoff ได้
- configuration ที่ไม่เก็บ production credential เป็น plaintext
- unit tests และ real loopback integration tests
- แนวทางรันแบบไม่มี console ระหว่างพัฒนา โดยยังคงวินิจฉัยปัญหาได้จาก rotating log

## 2. หลักการและขอบเขตความเชื่อถือ

Agent นี้เป็น managed endpoint component:

- เครื่องต้องผ่านขั้นตอน enrollment ก่อนเชื่อมใช้งาน
- controller สามารถระบุและ revoke device credential รายเครื่องได้
- agent มี identity และ lifecycle event ที่ตรวจสอบย้อนหลังได้
- administrator ของเครื่องสามารถตรวจพบ process, configuration และ log ได้
- agent ไม่แพร่ตัวเองไปยังเครื่องอื่น ไม่หลบระบบตรวจจับ และไม่เปลี่ยน security controls

## 3. Non-goals ของ Phase 1

Phase นี้ยังไม่เพิ่ม:

- command execution หรือ command catalog ใหม่
- persistence, auto-install หรือ Windows Service
- keylogging, screen capture, AV modification หรือ legacy startup side effects
- Unified Command Center UI
- Discord logic ภายใน agent process
- PyInstaller production release
- protocol migration ครั้งใหญ่

## 4. เหตุผลที่ไม่ใช้ Legacy Entry Point

`client/PhantomLink.py` รวม network client และ startup side effects หลายชนิดไว้ในไฟล์เดียว การเรียก `PhantomLink.main()` จาก Managed Agent จะทำให้ lifecycle ใหม่ผูกกับพฤติกรรมที่อยู่นอก Phase 1

จึงสร้าง entry point ใหม่และไม่เรียก legacy `main()` โดย Phase 1 จะ reuse เฉพาะ transport/encryption contract ที่แยกและทดสอบได้เท่านั้น

## 5. Architecture

### 5.1 ไฟล์หลัก

| ไฟล์ | หน้าที่ |
|---|---|
| `client/managed_agent.py` | entry point; ประกอบ config, credential store, connector และ runtime |
| `client/agent_runtime.py` | state machine, retry policy, heartbeat, socket ownership และ shutdown |
| `client/agent_config.py` | โหลด/validate non-secret config และเชื่อมต่อ credential provider |
| `tests/test_agent_runtime.py` | deterministic unit tests ด้วย fake clock/connector |
| `tests/test_agent_runtime_integration.py` | real TCP loopback tests |

อาจเพิ่ม shared transport module เฉพาะเมื่อจำเป็นต่อการ reuse framing/encryption โดยต้องรักษา byte contract เดิมและมี regression test ก่อนย้ายโค้ด

### 5.2 State machine

```text
STARTING
   |
   v
CONNECTING ---- transient failure ----> BACKOFF
   |                                  ^    |
   | authenticated                    |    | timer/event
   v                                  |    v
ONLINE -------- connection loss ------+ CONNECTING
   |
   | stop/revoked/fatal configuration
   v
STOPPED
```

สถานะที่อนุญาต:

- `STARTING`: validate config และโหลด device credential
- `CONNECTING`: เปิด socket และทำ handshake
- `ONLINE`: session พร้อม heartbeat และรับ protocol frames
- `BACKOFF`: รอ retry แบบ interruptible
- `STOPPED`: ปิด socket และ worker ทั้งหมดแล้ว

ทุก transition ต้องผ่าน runtime owner เพียงตัวเดียวและบันทึก structured lifecycle event

## 6. Runtime และ Thread Model

### 6.1 Agent process

- มี runtime owner thread หนึ่งตัว
- runtime owner เป็นผู้เปิด ปิด อ่าน และเขียน agent socket แต่เพียงผู้เดียว
- ใช้ `threading.Event` เป็น stop signal
- ใช้ `stop_event.wait(timeout)` แทน `time.sleep()` เพื่อหยุด backoff/heartbeat ได้ทันที
- state mutation ใช้ lock เดียวและไม่ถือ lock ระหว่าง blocking network I/O
- lifecycle logs ส่งผ่าน local queue หรือ synchronous local logger ที่มีเวลาทำงานจำกัด

Phase 1 ไม่มี command worker pool จึงไม่มีหลาย thread แข่งใช้ socket

### 6.2 Controller และ Discord

- Discord bot อยู่ฝั่ง controller ไม่อยู่ใน agent process
- controller เป็นเจ้าของ API/Discord event loop
- Discord ติดต่อ session ผ่าน controller API/state เท่านั้น
- Discord ไม่ถือหรือใช้งาน agent socket โดยตรง
- agent reconnect loop และ Discord asyncio loop จึงไม่แชร์ thread, event loop หรือ mutable socket state

## 7. Connection Lifecycle

1. โหลดและ validate non-secret config
2. โหลด device credential จาก credential provider
3. สร้าง socket ใหม่สำหรับ connection attempt
4. connect ภายใน configured connect timeout
5. ทำ authenticated handshake ตาม protocol contract
6. เมื่อสำเร็จ เปลี่ยนเป็น `ONLINE` และ reset backoff
7. ส่ง/รับ heartbeat ตาม interval
8. เมื่อ timeout, EOF, invalid frame หรือ socket error ให้ปิด socket เดิมก่อน
9. เปลี่ยนเป็น `BACKOFF` แล้วรอแบบ interruptible
10. retry จน stop, credential revoked หรือพบ fatal configuration/authentication error

ห้าม reuse socket หลัง connection attempt ล้มเหลว

## 8. Retry Policy

ค่าเริ่มต้น:

- base delay: 1 วินาที
- multiplier: 2
- maximum delay: 30 วินาที
- jitter: ±20% ของ delay ปัจจุบัน
- successful authenticated connection: reset delay เป็น base delay

ตัวอย่างช่วง nominal: `1 → 2 → 4 → 8 → 16 → 30 วินาที`

ประเภท failure:

| Failure | การตอบสนอง |
|---|---|
| DNS/network refused/timeout | ปิด socket และ retry |
| EOF/RST/heartbeat deadline | ปิด socket และ retry |
| malformed/oversized frame | ปิด session และ retry พร้อม protocol error event |
| credential rejected/revoked | หยุด retry และเข้าสู่ `STOPPED` |
| invalid local configuration | fail fast ก่อนเปิด socket |
| stop requested | ยกเลิก wait, ปิด socket และเข้าสู่ `STOPPED` |

## 9. Heartbeat และ Half-open Detection

OS TCP keepalive เพียงอย่างเดียวอาจใช้เวลานานเกินไปสำหรับการตรวจ half-open จึงใช้ application heartbeat เพิ่มเติม:

- reuse heartbeat contract เดิม: controller ส่ง `PING` และ agent ตอบ `PONG`
- ไม่เพิ่ม frame type หรือบังคับแก้ controller ใน Phase 1
- runtime บันทึกเวลาของ frame/heartbeat ล่าสุด
- หากเกิน read deadline โดยไม่มี frame ที่ยอมรับได้ ให้ถือว่า session เสีย
- close socket แล้วเข้าสู่ retry path เดียวกับ network failure
- heartbeat ไม่สร้าง thread เพิ่มและถูกจัดการโดย runtime owner

ค่า interval/deadline ต้อง config ได้และ validate ว่า deadline มากกว่า interval

## 10. Configuration และ Secret Storage

### 10.1 Non-secret config

เก็บในไฟล์ config ที่จำกัด ACL:

- controller host และ port
- agent identifier
- connect/read/heartbeat timeout
- retry base/max/jitter
- log path และ rotation limits

Server address ไม่ถือเป็น secret แต่ไฟล์ต้อง validate type/range และปฏิเสธค่าที่ผิดก่อนเริ่ม network I/O

### 10.2 Credential lifecycle

- `.env` ใช้ได้เฉพาะ development/test และห้ามบรรจุ production credential
- enrollment token เป็น one-time bootstrap secret
- controller แลก enrollment token เป็น device credential เฉพาะเครื่อง
- device credential เก็บด้วย Windows DPAPI/Credential Manager
- user-process phase ใช้ขอบเขต Current User
- หากย้ายเป็น Windows Service ภายหลัง ให้ทบทวน service identity, machine scope และ ACL ใหม่
- controller ต้อง revoke device credential รายเครื่องได้
- log และ exception message ต้อง redact token/credential

ไม่ hardcode credential และไม่ใช้ reversible encryption key ที่ฝังอยู่ใน executable เพราะสามารถ extract key และ plaintext คืนได้

## 11. Logging และ Observability

Agent ไม่มี popup และไม่พึ่ง stdout เมื่อรันผ่าน `pythonw.exe` จึงต้องมี rotating file log

Lifecycle event ขั้นต่ำ:

- process start/stop
- state transition
- connection attempt/success/failure category
- authentication accepted/rejected โดยไม่บันทึก credential
- heartbeat deadline
- reconnect delay ที่เลือก
- clean/forced socket close

Log ต้อง:

- มี timestamp, event name, state และ attempt number
- จำกัดขนาดและจำนวนไฟล์
- ไม่บันทึก command payload, token หรือ device credential ใน Phase 1
- เขียนล้มเหลวแล้วไม่ทำให้ runtime loop ตาย

## 12. Development และ Packaging

### 12.1 Development modes

- Debug: รัน `python.exe client/managed_agent.py` เพื่อเห็น traceback
- Background smoke test: รัน `pythonw.exe client/managed_agent.py` และตรวจ rotating log
- ทั้งสอง mode ใช้ code path เดียวกัน ต่างกันเฉพาะ console availability

### 12.2 Production packaging

เลื่อน PyInstaller production build ไปจนมี signing/release pipeline:

- ไม่ใช้ UPX
- ไม่ใช้ obfuscation เป็นมาตรการความปลอดภัย
- build reproducibly เท่าที่ toolchain รองรับ
- sign executable ด้วย trusted code-signing certificate
- publish SHA-256 และ build metadata
- สแกน artifact และมีขั้นตอน submit false-positive review
- เก็บ debug-symbol/build logs สำหรับวิเคราะห์เหตุการณ์

`console=False` ไม่ใช่กลไกหลบการตรวจจับ และไม่รับประกันผลของ AV reputation การออกแบบเน้น signed, explainable และ auditable artifact

## 13. Test Strategy

### 13.1 Unit tests

ใช้ fake connector, deterministic RNG และ fake/controlled wait interface เพื่อทดสอบ:

- transition ที่อนุญาตทั้งหมด
- exponential delay และ jitter bounds
- max delay cap
- reset หลัง authenticated connection
- fatal auth rejection หยุด retry
- config validation fail fast
- socket close ทุก failure path
- stop ระหว่าง connect, online และ backoff
- credential redaction ใน log

### 13.2 Real loopback integration tests

ใช้ TCP listener จริงบน `127.0.0.1` และ ephemeral port:

- agent เริ่มก่อน server แล้วเชื่อมได้เมื่อ server เปิด
- server ส่ง FIN ระหว่าง session
- server บังคับ RST ระหว่าง session
- proxy/listener หยุดส่งข้อมูลโดยไม่ปิด endpoint เพื่อจำลอง blackhole/half-open
- fragmented length header และ fragmented payload
- partial frame แล้วหยุดตอบ
- oversized/malformed frame
- server restart บน port เดิมแล้ว agent reconnect
- authenticated reconnect reset backoff
- shutdown ระหว่าง connect/read/backoff
- หลัง test ไม่มี runtime thread หรือ socket ค้าง

Integration tests ต้องใช้ timeout สั้นและ bounded เพื่อไม่ให้ CI hang

### 13.3 Project gates

- `compileall` ผ่าน
- unit และ integration tests ใหม่ผ่าน
- full existing test suite ผ่าน
- strict pytest ไม่มี unawaited coroutine หรือ unhandled thread exception
- `pythonw.exe` smoke test สร้าง lifecycle log และหยุดได้สะอาด
- protocol regression tests ยืนยัน byte framing/encryption contract เดิม

## 14. Error Handling

- network exceptions ถูกจัดหมวด ไม่ใช้ exception text เป็น control flow
- unexpected exception ปิด socket ก่อนเปลี่ยน state
- logger failure ไม่หยุด runtime
- config/credential failure ให้ fail fast พร้อม event ที่ redact แล้ว
- shutdown เป็น idempotent; เรียกซ้ำได้โดยไม่เกิด exception
- runtime ต้องมี final cleanup path ที่ปิด socket และตั้งสถานะ `STOPPED`

## 15. Compatibility

- ไม่เปลี่ยน command catalog เดิม
- ไม่เปลี่ยน C2/Discord behavior เดิมใน Phase 1
- ไม่ให้ Discord เข้าถึง agent socket
- transport/encryption contract เดิมต้องผ่าน regression tests
- legacy `PhantomLink.py` ยังคงอยู่และไม่ถูกเรียกจาก managed entry point

## 16. Rollout และ Rollback

Phase 1 rollout:

1. unit tests
2. loopback integration tests
3. foreground local smoke test
4. `pythonw.exe` background smoke test บนเครื่องทดสอบ
5. ตรวจ log, shutdown และ reconnect behavior

Rollback คือคืน entry point/config references ที่เปลี่ยนและลบไฟล์ agent foundation ใหม่ โดยไม่แตะ legacy client หรือ controller protocol

## 17. Acceptance Criteria

งาน Phase 1 สำเร็จเมื่อ:

1. Agent รัน foreground และ background ผ่าน entry point ใหม่ได้
2. ไม่มี legacy startup side effect ถูกเรียก
3. Agent reconnect หลัง server เริ่มช้า, restart, FIN, RST และ heartbeat deadline
4. Retry delay อยู่ใน exponential/jitter bounds และ reset หลัง auth สำเร็จ
5. Auth rejection/revocation หยุด retry
6. Stop request ยกเลิก backoff และ network wait ภายในเวลาที่ test กำหนด
7. Production credential ไม่ปรากฏใน `.env`, config file หรือ log
8. Agent และ Discord ไม่มี shared event loop/thread/socket ownership
9. Unit, loopback integration และ full existing suite ผ่าน
10. ไม่เหลือ thread หรือ socket หลัง shutdown

## 18. Future Phases

หลัง Phase 1 ผ่าน acceptance gates แล้วจึงออกแบบแยกสำหรับ:

- per-device enrollment API และ revocation UI เต็มรูปแบบ
- job ID, acknowledgement, timeout และ audit model
- Unified Command Center
- signed installer/Windows Service
- controlled command capability พร้อม policy และ authorization
