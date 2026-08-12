# Managed Background Agent Foundation — Design Specification

วันที่: 2026-08-12
สถานะ: Approved — 2026-08-12
ขอบเขต: Phase 1 — agent lifecycle foundation

## 1. เป้าหมาย

สร้างโครงสร้างพื้นฐานของ Managed Background Agent สำหรับ PhantomLink ที่ทำงานเบื้องหลังบน Windows เชื่อมต่อกลับไปยัง controller ได้อย่างเสถียร และเป็นฐานให้ Unified Command Center ในเฟสถัดไป

Phase 1 ต้องส่งมอบ:

- entry point แยกจาก legacy client
- lifecycle state machine ที่สังเกตและทดสอบได้
- reconnect/retry พร้อม exponential backoff และ jitter
- application heartbeat และ read deadline
- bounded socket polling ที่ตรวจ stop signal และ deadline ได้แม้ peer เงียบ
- side-effect-free client transport module ที่ legacy และ managed client ใช้ร่วมกัน
- clean shutdown ที่ยกเลิก connect/read/backoff ได้
- configuration ที่ไม่เก็บ production credential เป็น plaintext
- first-run enrollment flow และ authenticated managed handshake ที่มีผลตอบรับชัดเจน
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
- การย้าย legacy clients ไป managed protocol
- enrollment/revocation UI เต็มรูปแบบ; Phase 1 มีเฉพาะ endpoint และ CLI ขั้นต่ำ

## 4. เหตุผลที่ไม่ใช้ Legacy Entry Point

`client/PhantomLink.py` รวม network client และ startup side effects หลายชนิดไว้ในไฟล์เดียว การเรียก `PhantomLink.main()` จาก Managed Agent จะทำให้ lifecycle ใหม่ผูกกับพฤติกรรมที่อยู่นอก Phase 1

ไฟล์นี้ยังมี import ของ AV helper และสร้าง working directory ที่ module scope จึงไม่เหมาะเป็น dependency ของ managed entry point

จึงสร้าง entry point ใหม่และไม่ import หรือเรียก legacy `main()` จาก managed process โดย Phase 1 ต้องสกัด transport/encryption primitives ออกเป็นโมดูลที่ไม่มี side effect แล้วให้ทั้ง legacy และ managed client ใช้ร่วมกัน

## 5. Architecture

### 5.1 ไฟล์หลัก

| ไฟล์ | หน้าที่ |
|---|---|
| `client/managed_agent.py` | entry point; ประกอบ config, credential store, connector และ runtime |
| `client/agent_runtime.py` | state machine, retry policy, heartbeat, socket ownership และ shutdown |
| `client/agent_config.py` | โหลด/validate non-secret config และเชื่อมต่อ credential provider |
| `client/transport.py` | framing, incremental decode, key derivation และ encryption primitives ที่ไม่มี side effect |
| `C2/managed_auth.py` | managed-agent listener, enrollment endpoint core และ versioned handshake |
| `tests/test_agent_runtime.py` | deterministic unit tests ด้วย fake clock/connector |
| `tests/test_agent_runtime_integration.py` | real TCP loopback tests |
| `tests/test_client_transport.py` | byte/encryption compatibility ระหว่าง legacy, managed และ controller |
| `tests/test_managed_auth.py` | enrollment, replay protection และ explicit auth result |

`client/transport.py` เป็นข้อบังคับของ Phase 1 ไม่อนุญาตให้ managed runtime import `PhantomLink.py` หรือ copy framing/encryption ไปสร้าง implementation ชุดที่สอง

Legacy `ShellClient` ต้องเปลี่ยนเฉพาะจุดเรียก framing/encryption ให้ import primitives จากโมดูลใหม่นี้ พฤติกรรม wire เดิมต้องถูกตรึงด้วย regression tests ก่อนและหลังการสกัด

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
- `CONNECTING`: เปิด socket ทำ versioned handshake และรอ authenticated result
- `ONLINE`: session พร้อม heartbeat และรับ protocol frames
- `BACKOFF`: รอ retry แบบ interruptible
- `STOPPED`: ปิด socket และ worker ทั้งหมดแล้ว

ทุก transition ต้องผ่าน runtime owner เพียงตัวเดียวและบันทึก structured lifecycle event

## 6. Runtime และ Thread Model

### 6.1 Agent process

- มี runtime owner thread หนึ่งตัวและ logging writer thread หนึ่งตัว
- runtime owner เป็นผู้เปิด ปิด อ่าน และเขียน agent socket แต่เพียงผู้เดียว
- logging writer ไม่อ่านหรือแก้ state และไม่เข้าถึง socket
- ใช้ `threading.Event` เป็น stop signal
- ใช้ `stop_event.wait(timeout)` แทน `time.sleep()` เพื่อหยุด backoff/heartbeat ได้ทันที
- state mutation ใช้ lock เดียวและไม่ถือ lock ระหว่าง blocking network I/O
- lifecycle logs ส่งผ่าน bounded queue ไปยัง logging writer เท่านั้น

เมื่อเข้าสู่ `ONLINE` runtime ต้อง:

- ตั้ง `socket.settimeout(io_poll_interval)` โดยค่าเริ่มต้น `io_poll_interval = 1.0` วินาที
- เรียก `recv()` เป็นช่วงสั้นและเก็บข้อมูลใน persistent receive buffer
- มอง `socket.timeout` ว่าเป็น poll tick ไม่ใช่ disconnect
- หลังทุก poll tick ตรวจ `stop_event` และ `read_deadline`
- ไม่ใช้ blocking `recv_exactly()` ที่ทิ้ง partial frame เมื่อ timeout
- ให้ `FrameDecoder` ใน `client/transport.py` ประกอบ fragmented header/payload ข้ามหลาย poll ได้
- ใช้ bounded socket timeout เดียวกันกับ send path เพื่อไม่ให้ `sendall()` ค้างไม่มีกำหนด

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
4. ตั้ง connect timeout แล้ว connect ไป managed-agent port
5. ทำ managed TLS handshake และรอ `AUTH_OK` หรือ `AUTH_REJECT` ผ่าน TLS channel
6. หลัง `AUTH_OK` ให้ตั้ง socket timeout เป็น `io_poll_interval`
7. เปลี่ยนเป็น `ONLINE` และ reset backoff
8. รับ frame แบบ incremental, ตอบ heartbeat และตรวจ read deadline ทุก poll tick
9. เมื่อ EOF, invalid frame, deadline หรือ socket error ให้ปิด socket เดิมก่อน
10. เปลี่ยนเป็น `BACKOFF` แล้วรอแบบ interruptible
11. retry จน stop, credential revoked หรือพบ fatal configuration/authentication error

ห้าม reuse socket หลัง connection attempt ล้มเหลว

### 7.1 Managed handshake และ explicit authentication result

Legacy handshake ปัจจุบันปิด socket เมื่อ credential ผิดแต่ไม่ส่งผล `AUTH_OK/AUTH_REJECT` ทำให้ client แยก auth failure ออกจาก network failure ไม่ได้ Phase 1 จึงเพิ่ม managed TLS listener บน port แยกจาก legacy listener เพื่อไม่เปลี่ยน legacy wire behavior

Managed handshake version 1:

1. Agent เปิด TLS connection และตรวจ certificate/public-key pin ก่อนส่ง credential proof
2. Agent ส่ง framed hello ที่มี protocol version, agent ID และ credential key ID; ไม่มี secret ใน hello
3. Controller lookup device credential และส่ง random challenge nonce ผ่าน TLS
4. Agent ส่ง HMAC-SHA256 proof ครอบ canonical length-prefixed encoding ของ version, agent ID, key ID และ nonce
5. Controller ตรวจ proof ด้วย constant-time comparison
6. Controller ส่ง `AUTH_OK` หรือ `AUTH_REJECT` ผ่าน authenticated TLS channel
7. Agent เปลี่ยนเป็น `ONLINE` และ reset backoff หลังรับ `AUTH_OK` สำเร็จเท่านั้น
8. `AUTH_REJECT` จาก pinned TLS peer ถือเป็น fatal credential error และหยุด retry; timeout/EOF ก่อนผล auth ถือเป็น transient network failure

Nonce ต้องสุ่มใหม่ทุก connection attempt เพื่อป้องกัน replay การทดสอบต้องยืนยันว่า proof จาก session เก่าใช้ซ้ำไม่ได้ TLS เป็น encryption/authentication layer ของ managed transport; legacy SecretBox behavior ยังคงเดิมสำหรับ legacy listener

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

- โค้ด controller ปัจจุบันถูกตรวจแล้วว่าเริ่มส่ง `PING` ครั้งแรกหลังประมาณ 10 วินาที รอ `PONG` สูงสุด 10 วินาที และเว้นประมาณ 30 วินาทีก่อนรอบถัดไป
- managed listener ต้องรักษา contract เดียวกัน: controller ส่ง `PING` และ agent ตอบ `PONG`
- heartbeat support เป็น requirement ของ managed listener ไม่ใช่ optional behavior
- ค่าเริ่มต้น `controller_ping_interval = 30` วินาที, `controller_pong_timeout = 10` วินาที และ `agent_read_deadline = 90` วินาที
- config validation ต้องบังคับ `agent_read_deadline >= 3 * controller_ping_interval`
- runtime บันทึกเวลาของ frame/heartbeat ล่าสุด
- หากเกิน read deadline โดยไม่มี frame ที่ยอมรับได้ ให้ถือว่า session เสีย
- close socket แล้วเข้าสู่ retry path เดียวกับ network failure
- heartbeat ไม่สร้าง thread เพิ่มและถูกจัดการโดย runtime owner

ใน Phase 1 managed agent ไม่มี command execution จึงไม่มีช่วง `command_in_progress` ที่เลื่อน heartbeat Integration test ต้องใช้ managed listener จริงเพื่อพิสูจน์ cadence และป้องกัน connection flapping

## 10. Configuration และ Secret Storage

### 10.1 Non-secret config

เก็บในไฟล์ config ที่จำกัด ACL:

- controller host และ port
- managed-agent port และ enrollment HTTPS endpoint
- certificate/public-key pin ของ controller สำหรับ managed TLS และ enrollment HTTPS
- agent identifier
- connect timeout, I/O poll interval และ heartbeat/read deadline
- retry base/max/jitter
- log path และ rotation limits

Server address และ certificate/public-key pin ไม่ถือเป็น secret แต่ไฟล์ต้อง validate type/range และปฏิเสธค่าที่ผิดก่อนเริ่ม network I/O โดยบังคับ `0 < io_poll_interval <= 1.0`, timeout ทุกค่าเป็นบวก และ `agent_read_deadline >= 3 * controller_ping_interval`

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

### 10.3 First-run enrollment interface

Background mode ห้าม prompt หรือรับ token ผ่าน process command line

CLI ที่กำหนด:

```text
python.exe client/managed_agent.py enroll
python.exe client/managed_agent.py enroll --token-file PATH
python.exe client/managed_agent.py run
pythonw.exe client/managed_agent.py run
```

- `enroll` ทำงาน foreground เท่านั้น ต้องตรวจ `sys.stdin.isatty()` ก่อนรับ token ผ่าน `getpass()` และ fail fast เมื่อไม่มี interactive TTY
- `--token-file` ใช้สำหรับ automation โดยไฟล์ต้องเป็น absolute path, เป็น regular file, owner ตรงกับผู้รัน และ ACL ไม่เปิดกว้าง
- อ่าน token ได้ครั้งเดียวแล้วลบไฟล์ทันที; หาก exchange ล้มเหลวต้อง provision token ใหม่
- ไม่รองรับ `--enrollment-token VALUE`, stdin pipe หรือ environment variable เพื่อไม่ให้ token ปรากฏใน command history/process metadata
- Enrollment client ติดต่อ controller ผ่าน HTTPS พร้อม certificate/public-key pin ที่ provision อยู่ใน non-secret config
- Controller เก็บเฉพาะ hash ของ one-time token พร้อม expiry และ consumed flag
- เมื่อแลกสำเร็จ controller คืน agent ID, key ID และ device credential; agent เก็บ credential ด้วย DPAPI ก่อนรายงาน success
- Token ถูก mark consumed หลัง controller ยืนยัน issuance สำเร็จเพียงครั้งเดียว
- `run` ต้อง fail fast ด้วย event `ENROLLMENT_REQUIRED` หากไม่พบ DPAPI credential และห้ามเข้า reconnect loop
- `pythonw.exe` รองรับเฉพาะ `run`; enrollment ผ่าน `python.exe` เท่านั้น

Phase 1 จึงต้องมี enrollment endpoint ขั้นต่ำและ token-issuance CLI ฝั่ง controller แต่ยังไม่มี enrollment UI

## 11. Logging และ Observability

Agent ไม่มี popup และไม่พึ่ง stdout เมื่อรันผ่าน `pythonw.exe` จึงต้องมี rotating file log

Logging architecture:

- runtime ใช้ `QueueHandler` เขียนลง bounded queue แบบไม่ block
- `QueueListener` หนึ่งตัวเป็นเจ้าของ file handler เพียงตัวเดียว; ห้ามเปิด log path เดียวกันจากหลาย handler/process
- queue เต็มให้ drop event ตามนโยบายที่นับจำนวนได้และเขียน summary เมื่อระบบฟื้น ห้าม block socket owner
- file handler ต้องเป็น resilient wrapper รอบ `RotatingFileHandler`
- หาก rollover เจอ `PermissionError`/`WinError 32` ให้หยุด rollover รอบนั้น, reopen base file แบบ append และหน่วง rollover attempt ถัดไปเพื่อป้องกัน error spam
- background mode ตั้ง `logging.raiseExceptions = False`; logging failure ถูกนับแต่ไม่ propagate กลับ runtime
- shutdown ต้อง stop/flush `QueueListener` แบบ bounded แล้วจึงจบ process

`QueueHandler/QueueListener` ลด contention และแยก I/O ออกจาก runtime แต่ไม่ได้แก้ external file lock ด้วยตัวเอง จึงต้องมี rollover recovery ข้างต้น

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

- Debug: รัน `python.exe client/managed_agent.py run` เพื่อเห็น traceback
- Enrollment: รัน `python.exe client/managed_agent.py enroll` ใน interactive terminal
- Background smoke test: รัน `pythonw.exe client/managed_agent.py run` และตรวจ rotating log
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
- `socket.timeout` ทำหน้าที่เป็น poll tick และไม่ล้าง partial receive buffer
- fragmented frame ประกอบต่อได้ข้ามหลาย poll tick
- logging queue เต็มและ rollover failure ไม่ propagate เข้า runtime
- enrollment mode ปฏิเสธ missing TTY, insecure token file และ missing certificate pin

### 13.2 Real loopback integration tests

ใช้ TCP listener จริงบน `127.0.0.1` และ ephemeral port:

- agent เริ่มก่อน server แล้วเชื่อมได้เมื่อ server เปิด
- server ส่ง FIN ระหว่าง session
- server บังคับ RST ระหว่าง session
- proxy/listener หยุดส่งข้อมูลโดยไม่ปิด endpoint เพื่อจำลอง blackhole/half-open
- peer เงียบขณะ runtime สั่ง stop; runtime ต้องออกจาก `recv()` ภายใน `io_poll_interval` บวก tolerance
- fragmented length header และ fragmented payload
- fragmented frame ที่เว้นช่วงนานกว่า poll interval แต่สั้นกว่า read deadline
- partial frame แล้วหยุดตอบ
- oversized/malformed frame
- managed listener ส่ง PING ตาม contract และ agent ตอบ PONG โดยไม่ connection flap
- managed listener หยุดส่ง PING แล้ว agent ตัด session เมื่อถึง read deadline
- server restart บน managed port เดิมแล้ว agent reconnect
- authenticated reconnect reset backoff
- `AUTH_OK` ผ่าน pinned TLS channel reset backoff; authenticated `AUTH_REJECT` หยุด retry
- replay proof จาก nonce เก่าถูกปฏิเสธ
- enrollment HTTPS test server แลก one-time token ได้ครั้งเดียวและ reuse ไม่ได้
- Windows-only rollover test ถือ handle ของ rotated target ไว้เพื่อจำลอง `WinError 32` และยืนยันว่า runtime/log writer ฟื้นได้
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
- clean test environment ไม่มี import จาก `client/PhantomLink.py` ใน managed entry point

## 14. Error Handling

- network exceptions ถูกจัดหมวด ไม่ใช้ exception text เป็น control flow
- unexpected exception ปิด socket ก่อนเปลี่ยน state
- logger failure ไม่หยุด runtime
- config/credential failure ให้ fail fast พร้อม event ที่ redact แล้ว
- shutdown เป็น idempotent; เรียกซ้ำได้โดยไม่เกิด exception
- runtime ต้องมี final cleanup path ที่ปิด socket และตั้งสถานะ `STOPPED`

## 15. Compatibility

- ไม่เปลี่ยน command catalog เดิม
- legacy C2 listener และ Discord behavior เดิมต้องไม่เปลี่ยน
- เพิ่ม managed listener บน port แยกและ enrollment HTTPS endpoint ขั้นต่ำ
- ไม่ให้ Discord เข้าถึง agent socket
- legacy transport/encryption contract เดิมต้องผ่าน regression tests ก่อนและหลังย้าย primitives ไป `client/transport.py`
- managed handshake เป็น versioned protocol แยกและต้องมี explicit auth result ผ่าน pinned TLS channel
- legacy `PhantomLink.py` ยังคงอยู่และไม่ถูกเรียกจาก managed entry point

## 16. Rollout และ Rollback

Phase 1 rollout:

1. unit tests
2. loopback integration tests
3. สร้าง one-time token ด้วย controller CLI
4. foreground enrollment ผ่าน pinned HTTPS endpoint
5. foreground local runtime smoke test
6. `pythonw.exe` background runtime smoke test บนเครื่องทดสอบ
7. ตรวจ log, shutdown, heartbeat และ reconnect behavior

Rollback คือปิด managed listener/enrollment endpoint, คืน legacy client imports ไป implementation เดิมหากจำเป็น และลบไฟล์ agent foundation ใหม่ โดย legacy listener ต้องยังใช้งานได้ตลอด rollback

## 17. Acceptance Criteria

งาน Phase 1 สำเร็จเมื่อ:

1. Agent รัน foreground และ background ผ่าน entry point ใหม่ได้
2. ไม่มี legacy startup side effect ถูกเรียก
3. Runtime ตรวจ stop signal/read deadline อย่างน้อยทุก `io_poll_interval` แม้ peer เงียบ
4. Fragmented frame ไม่สูญหายเมื่อเกิด socket poll timeout
5. Agent reconnect หลัง server เริ่มช้า, restart, FIN, RST และ heartbeat deadline
6. Managed controller ส่ง PING ตาม cadence และ normal session ไม่เกิด connection flapping
7. Retry delay อยู่ใน exponential/jitter bounds และ reset หลังรับ `AUTH_OK` ผ่าน pinned TLS channel สำเร็จ
8. Auth rejection/revocation ที่ยืนยันตัวตนได้หยุด retry
9. One-time enrollment token ใช้ซ้ำไม่ได้และ production credential อยู่ใน DPAPI store เท่านั้น
10. Stop request ยกเลิก backoff และ network wait ภายในเวลาที่ test กำหนด
11. Production credential ไม่ปรากฏใน `.env`, config file, command line หรือ log
12. Logging rollover failure ไม่ทำให้ runtime ตายหรือสร้าง error spam ไม่จำกัด
13. Agent และ Discord ไม่มี shared event loop/thread/socket ownership
14. Legacy wire behavior ไม่เปลี่ยนหลังสกัด `client/transport.py`
15. Unit, loopback integration และ full existing suite ผ่าน
16. ไม่เหลือ runtime/logging thread หรือ socket หลัง shutdown

## 18. Future Phases

หลัง Phase 1 ผ่าน acceptance gates แล้วจึงออกแบบแยกสำหรับ:

- enrollment/revocation UI เต็มรูปแบบ
- job ID, acknowledgement, timeout และ audit model
- Unified Command Center
- signed installer/Windows Service
- controlled command capability พร้อม policy และ authorization
