# Managed Background Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** ส่งมอบ Managed Background Agent Phase 1 บน Windows ที่ enroll ได้ เชื่อมต่อ managed TLS listener ได้ ตอบ heartbeat, reconnect แบบ bounded และหยุดได้สะอาด โดยไม่เรียก legacy startup side effects หรือเพิ่ม command execution

**Architecture:** สกัด byte framing/legacy SecretBox primitives เป็น `client/transport.py`, เพิ่ม managed TLS protocol บน port แยก และให้ runtime owner thread เป็นเจ้าของ socket เพียงตัวเดียว ส่วน logging ใช้ queue writer แยกหนึ่ง thread. Enrollment ใช้ one-time token ผ่าน pinned HTTPS, credential ฝั่ง agent เก็บด้วย Current User DPAPI และ controller registry เก็บ device key แบบ DPAPI-encrypted.

**Tech Stack:** Python 3.12, stdlib `socket`/`ssl`/`http.server`/`logging`/`threading`, PyNaCl 1.5, cryptography 41, pywin32 306 บน Windows, pytest 9

## Global Constraints

- Phase 1 ไม่มี command execution, persistence, Windows Service, keylogging, screen capture, AV modification หรือ Unified Command Center UI.
- Phase 1 ไม่สร้าง PyInstaller artifact; ใช้ `python.exe`/`pythonw.exe` จนกว่าจะมี code-signing pipeline.
- Managed entry point ต้องไม่ import `client/PhantomLink.py`; legacy client และ listener ต้องรักษา wire behavior เดิม.
- Legacy wire behavior ต้องผ่าน regression tests ก่อนและหลัง transport extraction.
- Runtime ใช้ socket owner thread เดียว; logging writer ไม่แตะ socket/state.
- `io_poll_interval` ค่าเริ่มต้น 1.0 วินาทีและต้องอยู่ในช่วง `0 < value <= 1.0`.
- Heartbeat defaults: controller PING 30 วินาที, PONG timeout 10 วินาที, agent read deadline 90 วินาที.
- Retry defaults: base 1 วินาที, multiplier 2, cap 30 วินาที, jitter ±20%.
- Production credential ห้ามอยู่ใน `.env`, JSON config, command line หรือ log.
- ใช้ dependency ที่ติดตั้งแล้วเท่านั้น; ไม่เพิ่ม package ใหม่.
- ก่อนเริ่ม Task 1 ให้บันทึก `git status --short` และ `git diff --binary` ลง `debug-artifacts/managed-agent-preflight/`; ห้าม stage ไฟล์นอก task และห้ามทับ working changes ปัจจุบันใน `C2/C2.py`, `client/PhantomLink.py`, `C2/crypto.py` และ `tests/test_encryption.py`.
- ทุก commit ต้องตรวจ `git diff --cached --name-only` ให้ตรงกับรายการ Files ของ task นั้น.

---

## File Map

| Path | Responsibility |
|---|---|
| `client/transport.py` | legacy-compatible framing/SecretBox, managed auth proof encoding และ incremental `FrameDecoder` |
| `client/agent_config.py` | JSON config validation, identity update และ Current User DPAPI credential store |
| `client/agent_runtime.py` | state machine, TLS connector, retry, heartbeat, socket ownership และ shutdown |
| `client/agent_logging.py` | bounded queue logging และ Windows-resilient rollover |
| `client/managed_agent.py` | `enroll`/`run` CLI, pinned HTTPS enrollment และ composition root |
| `C2/managed_auth.py` | one-time token store, DPAPI device registry, managed handshake/listener และ enrollment HTTPS handler |
| `config.py` | optional managed listener/controller paths and ports |
| `C2/C2.py` | start managed services only when TLS configuration is complete |
| `tests/test_client_transport.py` | byte compatibility and incremental framing |
| `tests/test_agent_config.py` | validation, atomic config and DPAPI abstraction |
| `tests/test_managed_auth.py` | token, replay, explicit auth result and registry tests |
| `tests/test_agent_runtime.py` | deterministic state/retry/stop tests |
| `tests/test_agent_logging.py` | queue overflow and rollover recovery tests |
| `tests/test_agent_runtime_integration.py` | real TLS/TCP/HTTPS loopback behavior |
| `docs/runbooks/managed-agent-phase1.md` | enrollment, foreground/background run, verification and rollback commands |
| `scripts/rollback-managed-agent.ps1` | disable managed endpoints and restore the recorded feature patch |
| `debug-artifacts/managed-agent.patch` | exact reversible patch for Tasks 1-6 |
| `debug-artifacts/managed-agent-verification.md` | exact baseline/modified commands, outputs and exit statuses |

---

### Task 1: Side-effect-free transport extraction

**Files:**
- Create: `client/transport.py`
- Modify: `client/PhantomLink.py:804-880`
- Modify: `C2/C2.py:20-30,289-310`
- Preserve/add: `C2/crypto.py`
- Test: `tests/test_client_transport.py`
- Test: `tests/test_encryption.py`

**Interfaces:**
- Produces: `MAX_FRAME_SIZE`, `encode_message(data) -> tuple[bytes, bytes]`, `encode_json_payload(mapping) -> bytes`, `decode_json_payload(payload) -> dict`, `derive_key(password) -> bytes`, `encrypt(key, data) -> bytes`, `decrypt(key, data) -> bytes | None`, `canonical_auth_input(...) -> bytes`, `build_proof(...) -> bytes`, `verify_proof(...) -> bool`, `FrameDecoder.feed(chunk) -> list[bytes]`.
- Preserves: aliases `_derive_key`, `_encrypt`, `_decrypt` in `client/PhantomLink.py` for current callers/tests.

- [ ] **Step 1: Capture the existing uncommitted transport work before editing**

```powershell
New-Item -ItemType Directory -Force debug-artifacts/managed-agent-preflight | Out-Null
git status --short | Set-Content -Encoding utf8 debug-artifacts/managed-agent-preflight/status.txt
git diff --binary | Set-Content -Encoding utf8 debug-artifacts/managed-agent-preflight/worktree.patch
git rev-parse HEAD | Set-Content -Encoding ascii debug-artifacts/managed-agent-preflight/feature-base.txt
git diff --check
```

Expected: `git diff --check` exits 0; snapshot files contain the pre-existing edits without staging them.

- [ ] **Step 2: Write failing transport tests**

```python
from client.transport import FrameDecoder, MAX_FRAME_SIZE, derive_key, encode_message


def test_frame_decoder_keeps_partial_data_across_poll_ticks():
    header, payload = encode_message(b"PONG")
    decoder = FrameDecoder()
    assert decoder.feed(header[:2]) == []
    assert decoder.feed(header[2:] + payload[:1]) == []
    assert decoder.feed(payload[1:]) == [b"PONG"]


def test_frame_decoder_rejects_oversized_frame():
    decoder = FrameDecoder()
    oversized = (MAX_FRAME_SIZE + 1).to_bytes(4, "big")
    with pytest.raises(ValueError, match="frame too large"):
        decoder.feed(oversized)


def test_legacy_key_matches_controller():
    from C2.crypto import derive_key as controller_derive_key
    assert derive_key("pw") == controller_derive_key("pw")
```

- [ ] **Step 3: Run the focused tests and confirm the new module is missing**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_client_transport.py tests/test_encryption.py -q
```

Expected: collection fails with `ModuleNotFoundError: No module named 'client.transport'`.

- [ ] **Step 4: Implement the minimum transport module**

```python
MAX_FRAME_SIZE = 10 * 1024 * 1024
_DOMAIN = b"phantomlink-c2-v1"


def encode_message(data):
    payload = data.encode("utf-8") if isinstance(data, str) else bytes(data)
    return len(payload).to_bytes(4, "big"), payload


class FrameDecoder:
    def __init__(self, max_size=MAX_FRAME_SIZE):
        self.max_size = max_size
        self.buffer = bytearray()
        self.expected = None

    def feed(self, chunk):
        self.buffer.extend(chunk)
        frames = []
        while True:
            if self.expected is None:
                if len(self.buffer) < 4:
                    return frames
                self.expected = int.from_bytes(self.buffer[:4], "big")
                del self.buffer[:4]
                if self.expected > self.max_size:
                    raise ValueError("frame too large")
            if len(self.buffer) < self.expected:
                return frames
            frames.append(bytes(self.buffer[: self.expected]))
            del self.buffer[: self.expected]
            self.expected = None
```

Move the existing `derive_key`/`encrypt`/`decrypt` bodies unchanged into this file. In `PhantomLink.py`, import them with package/script fallbacks and aliases; remove the duplicated bodies and their now-unused crypto imports.

Add `canonical_auth_input`, `build_proof` and `verify_proof` here using length-prefixed fields plus HMAC-SHA256 so agent and controller cannot drift. Keep the current `C2/crypto.py` implementation and current `C2/C2.py` encryption calls, but make `C2/crypto.py` re-export the three legacy crypto primitives from `client.transport` instead of maintaining a second implementation.

Add canonical JSON helpers using UTF-8, `sort_keys=True`, `separators=(",", ":")`; decoding must require a JSON object and reject invalid UTF-8, arrays and values over the caller's 64 KiB managed-protocol cap.

- [ ] **Step 5: Verify transport compatibility and absence of import side effects**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_client_transport.py tests/test_encryption.py tests/test_phantomlink_config_import.py -q
.\.venv\Scripts\python.exe -c "import client.transport; assert 'client.PhantomLink' not in __import__('sys').modules"
```

Expected: both commands exit 0.

- [ ] **Step 6: Commit only the transport slice**

```powershell
git add client/transport.py client/PhantomLink.py C2/C2.py C2/crypto.py tests/test_client_transport.py tests/test_encryption.py
git diff --cached --check
git diff --cached --name-only
git commit -m "refactor: extract side-effect-free client transport"
```

Expected staged paths: exactly the six paths above.

---

### Task 2: Validated config and Current User DPAPI credential store

**Files:**
- Create: `client/agent_config.py`
- Create: `tests/test_agent_config.py`

**Interfaces:**
- Produces: immutable `AgentConfig`, `DeviceCredential`, `validate_private_file(path, acl_inspector)`, `load_config(path)`, `write_identity(path, agent_id, key_id)`, `DpapiCredentialStore.load/save/delete`.
- Consumes later: Task 5 runtime and Task 6 CLI use these exact classes.

- [ ] **Step 1: Write failing validation and credential tests**

```python
class FakeProtector:
    def protect(self, data):
        return b"protected:" + data[::-1]

    def unprotect(self, data):
        assert data.startswith(b"protected:")
        return data[len(b"protected:") :][::-1]


def valid_config(tmp_path):
    return {
        "controller_host": "127.0.0.1",
        "managed_port": 5443,
        "enrollment_port": 5444,
        "tls_cert_sha256": "ab" * 32,
        "io_poll_interval": 1.0,
        "controller_ping_interval": 30.0,
        "controller_pong_timeout": 10.0,
        "agent_read_deadline": 90.0,
        "connect_timeout": 5.0,
        "retry_base": 1.0,
        "retry_max": 30.0,
        "retry_jitter": 0.2,
        "log_path": str(tmp_path / "agent.log"),
        "log_max_bytes": 1048576,
        "log_backup_count": 5,
    }


def test_rejects_poll_interval_over_one(tmp_path):
    data = valid_config(tmp_path)
    data["io_poll_interval"] = 1.01
    with pytest.raises(ValueError, match="io_poll_interval"):
        AgentConfig.from_mapping(data)


def test_rejects_short_read_deadline(tmp_path):
    data = valid_config(tmp_path)
    data["agent_read_deadline"] = 89
    with pytest.raises(ValueError, match="agent_read_deadline"):
        AgentConfig.from_mapping(data)


def test_credential_store_round_trip(tmp_path):
    protector = FakeProtector()
    store = DpapiCredentialStore(tmp_path / "credential.bin", protector)
    expected = DeviceCredential("agent-1", "key-1", b"x" * 32)
    store.save(expected)
    assert store.load() == expected
    assert b"x" * 32 not in (tmp_path / "credential.bin").read_bytes()


def test_config_rejects_world_writable_acl(tmp_path):
    path = tmp_path / "agent.json"
    path.write_text(json.dumps(valid_config(tmp_path)), "utf-8")
    with pytest.raises(ValueError, match="ACL"):
        load_config(path, acl_inspector=lambda _: {"owner": True, "world_write": True})
```

- [ ] **Step 2: Run the focused test and verify it fails**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_agent_config.py -q
```

Expected: collection fails because `client.agent_config` does not exist.

- [ ] **Step 3: Implement exact config and credential types**

```python
@dataclass(frozen=True)
class DeviceCredential:
    agent_id: str
    key_id: str
    secret: bytes


@dataclass(frozen=True)
class AgentConfig:
    controller_host: str
    managed_port: int
    enrollment_port: int
    tls_cert_sha256: str
    agent_id: str = ""
    key_id: str = ""
    connect_timeout: float = 5.0
    io_poll_interval: float = 1.0
    controller_ping_interval: float = 30.0
    controller_pong_timeout: float = 10.0
    agent_read_deadline: float = 90.0
    retry_base: float = 1.0
    retry_max: float = 30.0
    retry_jitter: float = 0.2
    log_path: str = "managed-agent.log"
    log_max_bytes: int = 1048576
    log_backup_count: int = 5
```

Validation must reject empty host, ports outside 1..65535, non-64-character lowercase hex pin, non-positive timeouts, poll outside `(0, 1]`, retry max below base, jitter outside `[0, 1]`, and deadline below three ping intervals. `DpapiCredentialStore` uses injected `protect/unprotect` in tests and `win32crypt.CryptProtectData/CryptUnprotectData` in production; writes use temporary file plus `os.replace`.

`validate_private_file` requires a regular file owned by the current Windows user and rejects write permission for Everyone, Builtin Users and Authenticated Users. `load_config` calls it before parsing; `write_identity` and credential writes apply the same private DACL after `os.replace`.

- [ ] **Step 4: Run validation and serialization tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_agent_config.py -q
```

Expected: all tests pass and plaintext secret is absent from the stored blob.

- [ ] **Step 5: Commit the config boundary**

```powershell
git add client/agent_config.py tests/test_agent_config.py
git diff --cached --check
git commit -m "feat: add managed agent config and credential store"
```

---

### Task 3: One-time enrollment registry and managed authentication primitives

**Files:**
- Create: `C2/managed_auth.py`
- Create: `tests/test_managed_auth.py`

**Interfaces:**
- Produces: `EnrollmentStore.issue/consume`, `DeviceRegistry.enroll/get/revoke`.
- Consumes: `canonical_auth_input`, `build_proof` and `verify_proof` from `client.transport`; no controller-side copy is permitted.
- Data contract: device secret is exactly 32 random bytes; token is 32 URL-safe random bytes; stored token is SHA-256 only.

- [ ] **Step 1: Write failing one-time/replay/auth tests**

```python
class FakeProtector:
    def protect(self, data):
        return b"protected:" + data[::-1]

    def unprotect(self, data):
        return data[len(b"protected:") :][::-1]


def test_token_is_consumed_once(tmp_path):
    store = EnrollmentStore(tmp_path / "tokens.json", now=lambda: 1000)
    token = store.issue(ttl_seconds=60)
    assert store.consume(token) is True
    assert store.consume(token) is False
    assert token not in (tmp_path / "tokens.json").read_text("utf-8")


def test_auth_proof_is_bound_to_nonce():
    secret = b"s" * 32
    proof = build_proof(secret, 1, "agent", "key", b"nonce-a")
    assert verify_proof(secret, 1, "agent", "key", b"nonce-a", proof)
    assert not verify_proof(secret, 1, "agent", "key", b"nonce-b", proof)


def test_revoke_removes_device(tmp_path):
    registry = DeviceRegistry(tmp_path / "devices.bin", FakeProtector())
    credential = registry.enroll()
    assert registry.get(credential.agent_id, credential.key_id) == credential.secret
    registry.revoke(credential.agent_id, credential.key_id)
    assert registry.get(credential.agent_id, credential.key_id) is None
```

- [ ] **Step 2: Run and confirm the managed auth module is missing**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_managed_auth.py -q
```

Expected: collection fails with `ModuleNotFoundError: No module named 'C2.managed_auth'`.

- [ ] **Step 3: Import shared authentication and implement atomic stores**

```python
from client.transport import build_proof, verify_proof
```

`EnrollmentStore` guards read-modify-write with one `threading.Lock`, stores `{hash: {expires_at, consumed}}`, writes atomically, and marks consumed before returning success. `DeviceRegistry` stores its JSON payload as one DPAPI-protected blob and returns `DeviceCredential(agent_id=str(uuid4()), key_id=str(uuid4()), secret=secrets.token_bytes(32))`.

Add `EnrollmentService.exchange(token)` as the only production exchange path. Under one service lock it validates but does not consume the token, persists the device credential, then marks the token consumed; if the consumed-state write fails it revokes the just-created device before returning an error. The HTTPS handler in Task 4 must call this method rather than calling both stores independently.

- [ ] **Step 4: Verify token secrecy, expiry, replay and revocation**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_managed_auth.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit authentication primitives**

```powershell
git add C2/managed_auth.py tests/test_managed_auth.py
git diff --cached --check
git commit -m "feat: add one-time enrollment and managed auth"
```

---

### Task 4: Managed TLS listener, enrollment HTTPS and controller startup

**Files:**
- Modify: `C2/managed_auth.py`
- Modify: `config.py`
- Modify: `.env.example`
- Modify: `C2/C2.py:857-923`
- Modify: `tests/test_managed_auth.py`

**Interfaces:**
- Produces: `send_json_frame`, `recv_json_frame`, `ManagedServer.serve_forever(stop_event)`, `EnrollmentServer.serve_forever()`, `issue-token`/`revoke` CLI.
- Wire messages: length-prefixed UTF-8 JSON for `HELLO`, `CHALLENGE`, `AUTH_PROOF`, `AUTH_OK`, `AUTH_REJECT`; heartbeat payloads remain framed raw `PING`/`PONG`.

- [ ] **Step 1: Write failing TLS handshake and heartbeat tests**

```python
def test_managed_handshake_returns_explicit_results(tls_material, registry):
    server = start_managed_server(tls_material, registry)
    good = connect_test_agent(server.port, registry.credential)
    assert good.auth_result == "AUTH_OK"
    bad = connect_test_agent(server.port, replace(registry.credential, secret=b"z" * 32))
    assert bad.auth_result == "AUTH_REJECT"


def test_managed_server_sends_ping_and_accepts_pong(tls_material, registry):
    server = start_managed_server(tls_material, registry, ping_interval=0.05, pong_timeout=0.1)
    agent = connect_test_agent(server.port, registry.credential)
    assert agent.recv_frame() == b"PING"
    agent.send_frame(b"PONG")
    assert server.wait_for_heartbeat(registry.credential.agent_id, 0.5)
```

- [ ] **Step 2: Run the focused tests and verify missing server types**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_managed_auth.py -q
```

Expected: failure names `ManagedServer`/`EnrollmentServer` as undefined.

- [ ] **Step 3: Implement TLS listener and HTTPS enrollment**

Use `ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)`, set `minimum_version = ssl.TLSVersion.TLSv1_2`, and load the configured certificate/key. Each accepted managed connection gets one bounded handler thread that:

```python
hello = recv_json_frame(conn, timeout=10.0, max_size=65536)
nonce = secrets.token_bytes(32)
send_json_frame(conn, {"type": "CHALLENGE", "nonce": b64encode(nonce).decode()})
proof = recv_json_frame(conn, timeout=10.0, max_size=65536)
secret = registry.get(hello["agent_id"], hello["key_id"])
accepted = secret is not None and verify_proof(
    secret, 1, hello["agent_id"], hello["key_id"], nonce,
    b64decode(proof["proof"], validate=True),
)
send_json_frame(conn, {"type": "AUTH_OK" if accepted else "AUTH_REJECT"})
```

Both JSON frame helpers must delegate payload serialization/parsing to `client.transport.encode_json_payload/decode_json_payload`; only socket I/O remains in `C2/managed_auth.py`.

On `AUTH_OK`, wait 10 seconds before first PING, require PONG within 10 seconds, then wait 30 seconds. Tests inject shorter values. Enrollment handler accepts only `POST /v1/enroll`, consumes the token atomically, enrolls one device, and returns agent ID/key ID/base64 secret over TLS. It never logs the token or secret.

`ManagedServer.stop()` closes the listening socket and every active session, then joins all non-daemon accept/session threads within a caller-supplied timeout. `EnrollmentServer.shutdown()` and `server_close()` are always invoked in controller cleanup so integration tests can prove zero leaked threads.

- [ ] **Step 4: Add optional controller configuration and guarded startup**

Add these exact settings with disabled-by-default certificate paths:

```python
MANAGED_PORT = int(os.getenv("PHANTOMLINK_MANAGED_PORT", "5443"))
ENROLLMENT_PORT = int(os.getenv("PHANTOMLINK_ENROLLMENT_PORT", "5444"))
MANAGED_TLS_CERT = os.getenv("PHANTOMLINK_TLS_CERT", "")
MANAGED_TLS_KEY = os.getenv("PHANTOMLINK_TLS_KEY", "")
MANAGED_STORE = os.getenv("PHANTOMLINK_MANAGED_STORE", "managed-store")
```

`C2.main()` starts both managed services only when cert and key are non-empty existing files; otherwise it prints one `Managed services disabled` line and leaves the legacy listener/Discord behavior unchanged.

Keep a controller stop event and both server objects in `main()`. Its existing final shutdown path must call `managed_server.stop(timeout=5)` and `enrollment_server.shutdown()` before closing the legacy socket.

- [ ] **Step 5: Add and verify operator CLI**

```powershell
.\.venv\Scripts\python.exe -m C2.managed_auth issue-token --store .\debug-artifacts\managed-store --ttl 600
.\.venv\Scripts\python.exe -m C2.managed_auth list-devices --store .\debug-artifacts\managed-store
.\.venv\Scripts\python.exe -m C2.managed_auth revoke --store .\debug-artifacts\managed-store --agent-id agent-test --key-id key-test
```

Expected: first command prints one token once; second prints no secrets; third returns a nonzero not-found status for the sample IDs without revealing registry contents.

- [ ] **Step 6: Run focused and legacy controller tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_managed_auth.py tests/test_protocol_auth.py tests/test_c2_coverage.py -q
```

Expected: all tests pass; legacy protocol tests remain byte-compatible.

- [ ] **Step 7: Commit controller services**

```powershell
git add C2/managed_auth.py config.py .env.example C2/C2.py tests/test_managed_auth.py
git diff --cached --check
git commit -m "feat: add managed TLS and enrollment listeners"
```

---

### Task 5: Runtime state machine, bounded socket polling and reconnect

**Files:**
- Create: `client/agent_runtime.py`
- Create: `tests/test_agent_runtime.py`

**Interfaces:**
- Produces: `AgentState`, `AuthRejected`, `ManagedConnector.connect(config, credential)`, `RetryPolicy.next_delay/reset`, `send_frame(conn, payload)`, `AgentRuntime.run/stop/state`.
- Consumes: `AgentConfig`, `DeviceCredential`, `FrameDecoder`, `encode_message` and the exact `build_proof` from `client.transport`.

- [ ] **Step 1: Write failing deterministic runtime tests**

```python
def test_retry_sequence_and_reset():
    policy = RetryPolicy(base=1, maximum=30, jitter=0, random=lambda: 0.5)
    assert [policy.next_delay() for _ in range(7)] == [1, 2, 4, 8, 16, 30, 30]
    policy.reset()
    assert policy.next_delay() == 1


def test_socket_timeout_is_poll_tick_not_disconnect(runtime, fake_socket):
    fake_socket.recv.side_effect = [socket.timeout(), frame(b"PING"), socket.timeout()]
    runtime.run_one_session(fake_socket)
    assert fake_socket.sent_frames == [b"PONG"]


def test_partial_frame_survives_timeout(runtime, fake_socket):
    packet = frame(b"PING")
    fake_socket.recv.side_effect = [packet[:2], socket.timeout(), packet[2:], b""]
    runtime.run_one_session(fake_socket)
    assert fake_socket.sent_frames == [b"PONG"]


def test_auth_rejection_stops_without_backoff(runtime, connector):
    connector.connect.side_effect = AuthRejected()
    runtime.run()
    assert runtime.state is AgentState.STOPPED
    assert connector.calls == 1
```

- [ ] **Step 2: Run and confirm runtime module is missing**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_agent_runtime.py -q
```

Expected: collection fails with `ModuleNotFoundError: No module named 'client.agent_runtime'`.

- [ ] **Step 3: Implement states and retry policy**

```python
class AgentState(Enum):
    STARTING = auto()
    CONNECTING = auto()
    ONLINE = auto()
    BACKOFF = auto()
    STOPPED = auto()


class RetryPolicy:
    def next_delay(self):
        nominal = min(self.base * (2 ** self.failures), self.maximum)
        self.failures += 1
        spread = nominal * self.jitter
        return nominal - spread + (2 * spread * self.random())

    def reset(self):
        self.failures = 0
```

- [ ] **Step 4: Implement pinned TLS connector and explicit auth handling**

`ManagedConnector.connect` creates a fresh socket per attempt, applies `connect_timeout`, wraps with a TLS 1.2+ client context, compares `sha256(conn.getpeercert(binary_form=True)).hexdigest()` with `config.tls_cert_sha256` using `hmac.compare_digest`, performs HELLO/CHALLENGE/PROOF, and returns only after `AUTH_OK`. It raises `AuthRejected` only for authenticated `AUTH_REJECT`; EOF/timeout raises a transient `OSError`.

- [ ] **Step 5: Implement the owner loop and final cleanup**

```python
def run_one_session(self, conn):
    conn.settimeout(self.config.io_poll_interval)
    decoder = FrameDecoder(max_size=65536)
    deadline = self.clock() + self.config.agent_read_deadline
    while not self.stop_event.is_set():
        try:
            chunk = conn.recv(65536)
            if not chunk:
                raise ConnectionError("peer closed")
            for payload in decoder.feed(chunk):
                if payload != b"PING":
                    raise ValueError("unexpected online frame")
                deadline = self.clock() + self.config.agent_read_deadline
                send_frame(conn, b"PONG")
        except socket.timeout:
            pass
        if self.clock() >= deadline:
            raise TimeoutError("heartbeat deadline")
```

`run()` transitions only through the owner thread, closes the current socket in `finally`, uses `stop_event.wait(delay)` for backoff, resets retry after authenticated connect, and sets `STOPPED` in the outermost `finally`. `stop()` is idempotent and calls `shutdown(SHUT_RDWR)` on the current socket to interrupt a poll immediately.

`ManagedConnector` publishes the raw socket to `AgentRuntime` before `connect()` begins and clears it only after close, so `stop()` can interrupt DNS/connect/TLS/auth waits rather than waiting for the full connect timeout. Exception classes, not exception message strings, select fatal versus transient behavior.

- [ ] **Step 6: Verify state, stop, deadline and retry behavior**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_agent_runtime.py -q
```

Expected: all tests pass without leaked threads.

- [ ] **Step 7: Commit runtime foundation**

```powershell
git add client/agent_runtime.py tests/test_agent_runtime.py
git diff --cached --check
git commit -m "feat: add managed agent runtime state machine"
```

---

### Task 6: Queue logging, enrollment client and `enroll`/`run` CLI

**Files:**
- Create: `client/agent_logging.py`
- Create: `client/managed_agent.py`
- Create: `tests/test_agent_logging.py`
- Modify: `tests/test_agent_config.py`

**Interfaces:**
- Produces: `start_agent_logging(config) -> LoggingRuntime`, `LoggingRuntime.stop(timeout)`, `EnrollmentRejected`, `enroll(config, token, store)`, `main(argv=None) -> int`.
- CLI: `enroll`, `enroll --token-file PATH`, `run` exactly.

- [ ] **Step 1: Write failing logging and CLI tests**

```python
def test_rollover_permission_error_does_not_escape(tmp_path, monkeypatch):
    handler = ResilientRotatingFileHandler(tmp_path / "agent.log", maxBytes=1, backupCount=2)
    monkeypatch.setattr(handler, "doRollover", Mock(side_effect=PermissionError(32, "locked")))
    handler.emit(logging.makeLogRecord({"msg": "event"}))
    handler.emit(logging.makeLogRecord({"msg": "event-2"}))
    assert handler.rollover_failures == 1


def test_enroll_requires_tty_without_token_file(monkeypatch):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    assert main(["enroll"]) == 2


def test_run_without_credential_is_not_retried(config_path, empty_store, caplog):
    assert main(["run", "--config", str(config_path)]) == 3
    assert "ENROLLMENT_REQUIRED" in caplog.text
```

- [ ] **Step 2: Run and confirm logging/entry modules are missing**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_agent_logging.py tests/test_agent_config.py -q
```

Expected: collection fails for missing `client.agent_logging` or `client.managed_agent`.

- [ ] **Step 3: Implement bounded queue logging with resilient rollover**

Use `QueueHandler` with `queue.Queue(maxsize=1000)` and one `QueueListener`. Override `enqueue` with `put_nowait`; increment a protected dropped counter on `queue.Full`, then enqueue one `LOG_EVENTS_DROPPED` summary containing the count after queue recovery. Format each line as JSON with timestamp, event, state and attempt. `ResilientRotatingFileHandler.emit` attempts rollover only after `next_rollover_attempt`; on `PermissionError` including Windows `WinError 32` it increments once, sets the next attempt to monotonic time + 30 seconds, reopens the base file in append mode, and never re-raises. Set `logging.raiseExceptions = False` in `start_agent_logging`. `LoggingRuntime.stop(timeout)` reserves queue space for the listener sentinel, joins within the supplied timeout and reports a flush timeout without blocking the runtime owner indefinitely.

- [ ] **Step 4: Implement secure token acquisition and pinned HTTPS enrollment**

`enroll` without `--token-file` first requires `sys.stdin.isatty()` and then calls `getpass.getpass`. The file path mode requires an absolute path and delegates regular-file/owner/DACL checks to `agent_config.validate_private_file`. Read once with UTF-8, delete in `finally` before opening the network connection, and never include the token in exceptions/logs.

Use `http.client.HTTPSConnection` with a TLS 1.2+ context. Call `connect()`, compare the DER certificate SHA-256 pin before `POST /v1/enroll`, then send `{"token": token}`. Save returned credential with DPAPI before writing non-secret agent/key IDs and before logging success.

- [ ] **Step 5: Implement the exact CLI boundary**

```python
def parser():
    root = argparse.ArgumentParser()
    commands = root.add_subparsers(dest="command", required=True)
    enroll_cmd = commands.add_parser("enroll")
    enroll_cmd.add_argument("--config", default=str(default_config_path()))
    enroll_cmd.add_argument("--token-file")
    run_cmd = commands.add_parser("run")
    run_cmd.add_argument("--config", default=str(default_config_path()))
    return root
```

Return codes: 0 success/clean stop, 2 invalid CLI/enrollment input, 3 `ENROLLMENT_REQUIRED`, 4 invalid config, 5 enrollment/auth fatal error. `pythonw.exe` with `enroll` returns 2; `run` never prompts.

- [ ] **Step 6: Run focused CLI/logging tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_agent_logging.py tests/test_agent_config.py tests/test_agent_runtime.py -q
.\.venv\Scripts\python.exe client/managed_agent.py --help
```

Expected: tests pass and help lists only `enroll` and `run`.

- [ ] **Step 7: Commit entry point and logging**

```powershell
git add client/agent_logging.py client/managed_agent.py tests/test_agent_logging.py tests/test_agent_config.py
git diff --cached --check
git commit -m "feat: add managed agent enrollment and entry point"
```

---

### Task 7: Real loopback verification, runbook and runnable rollback

**Files:**
- Create: `tests/test_agent_runtime_integration.py`
- Create: `docs/runbooks/managed-agent-phase1.md`
- Create: `scripts/rollback-managed-agent.ps1`
- Create: `debug-artifacts/managed-agent.patch`
- Create: `debug-artifacts/managed-agent-verification.md`

**Interfaces:**
- Produces: a repeatable loopback proof, exact operator procedure and rollback command.
- Consumes: the complete Phase 1 interfaces from Tasks 1-6.

- [ ] **Step 1: Add a real ephemeral TLS fixture**

Generate a localhost RSA certificate at test runtime with installed `cryptography`, bind managed/enrollment listeners to `127.0.0.1` port 0, and compute the agent pin from DER bytes. Keep all timeouts at or below 0.5 seconds in tests.

The runbook must also include this exact pin calculation for an operator-provided PEM certificate:

```powershell
@'
from pathlib import Path
from hashlib import sha256
from cryptography import x509
from cryptography.hazmat.primitives.serialization import Encoding
cert = x509.load_pem_x509_certificate(Path("debug-artifacts/managed-cert.pem").read_bytes())
print(sha256(cert.public_bytes(Encoding.DER)).hexdigest())
'@ | .\.venv\Scripts\python.exe -
```

Then document: set the five `PHANTOMLINK_MANAGED_*`/TLS environment values, start `python.exe C2/C2.py`, issue a 600-second token, write the non-secret JSON config, run `python.exe client/managed_agent.py enroll`, run `python.exe client/managed_agent.py run`, and finally run `pythonw.exe client/managed_agent.py run` while checking the JSON lifecycle log.

- [ ] **Step 2: Write the required integration cases**

```python
@pytest.mark.parametrize("failure", ["fin", "rst", "silent"])
def test_reconnects_after_real_transport_failure(loopback_stack, failure):
    agent = loopback_stack.start_agent()
    loopback_stack.break_session(failure)
    assert loopback_stack.wait_for_authenticated_sessions(2, timeout=3.0)
    agent.stop()
    assert agent.join(2.0)


def test_fragmented_frame_across_poll_timeout(loopback_stack):
    agent = loopback_stack.start_agent(io_poll_interval=0.05, read_deadline=0.5)
    loopback_stack.send_fragmented_ping(delays=[0.0, 0.08, 0.08])
    assert loopback_stack.recv_frame(timeout=0.5) == b"PONG"


def test_one_time_enrollment_and_replay_rejection(loopback_stack):
    token = loopback_stack.issue_token()
    credential = loopback_stack.enroll(token)
    with pytest.raises(EnrollmentRejected):
        loopback_stack.enroll(token)
    old_proof = loopback_stack.capture_proof(credential)
    assert loopback_stack.replay_proof(old_proof) == "AUTH_REJECT"
```

Also cover: agent starts before server, real FIN and RST, stop while peer silent, heartbeat deadline, server restart on same port, authenticated backoff reset, malformed/oversized frame, token-file deletion, pinned-certificate mismatch, and zero runtime/logging threads after shutdown.

- [ ] **Step 3: Run focused integration tests repeatedly**

```powershell
1..3 | ForEach-Object {
  .\.venv\Scripts\python.exe -m pytest tests/test_agent_runtime_integration.py -q --maxfail=1
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
```

Expected: three consecutive exits 0 with no hang.

- [ ] **Step 4: Add Windows rollover and pythonw smoke verification**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_agent_logging.py -q
$pythonw = Resolve-Path .\.venv\Scripts\pythonw.exe
$proc = Start-Process $pythonw -ArgumentList 'client/managed_agent.py','run','--config','debug-artifacts/managed-agent-test.json' -WindowStyle Hidden -PassThru
Start-Sleep -Seconds 3
Stop-Process -Id $proc.Id
Wait-Process -Id $proc.Id -ErrorAction SilentlyContinue
Test-Path debug-artifacts/managed-agent.log
```

Expected: pytest exits 0, no console window appears, and the lifecycle log exists. Use only the loopback config/credential generated by the runbook; do not use `.env` credentials.

- [ ] **Step 5: Run all project gates**

```powershell
.\.venv\Scripts\python.exe -m compileall -q client C2
.\.venv\Scripts\python.exe -m pytest tests/test_client_transport.py tests/test_agent_config.py tests/test_managed_auth.py tests/test_agent_runtime.py tests/test_agent_logging.py tests/test_agent_runtime_integration.py -q
.\.venv\Scripts\python.exe -m pytest -q
```

Expected: every command exits 0; pytest output contains no unhandled thread exception warning.

- [ ] **Step 6: Generate the exact reversible feature patch**

```powershell
$base = (Get-Content debug-artifacts/managed-agent-preflight/feature-base.txt -Raw).Trim()
git diff $base..HEAD -- client/transport.py client/agent_config.py client/agent_runtime.py client/agent_logging.py client/managed_agent.py C2/managed_auth.py C2/crypto.py C2/C2.py config.py .env.example tests/test_client_transport.py tests/test_agent_config.py tests/test_managed_auth.py tests/test_agent_runtime.py tests/test_agent_logging.py tests/test_encryption.py --output=debug-artifacts/managed-agent.patch
$patch = (Resolve-Path debug-artifacts/managed-agent.patch).Path
$checkRoot = Join-Path $env:TEMP "managed-agent-patch-check-$PID"
if (-not ([IO.Path]::GetFullPath($checkRoot).StartsWith([IO.Path]::GetFullPath($env:TEMP), [StringComparison]::OrdinalIgnoreCase))) { throw 'Unsafe patch-check path' }
git worktree add --detach $checkRoot $base
Push-Location $checkRoot
try { git apply --check $patch; if ($LASTEXITCODE -ne 0) { throw 'Patch check failed' } }
finally { Pop-Location; git worktree remove --force $checkRoot }
```

Expected: patch is non-empty and `git apply --check` exits 0 in a clean worktree created at `$base`.

- [ ] **Step 7: Write exact verification record**

Record in `debug-artifacts/managed-agent-verification.md`:

```text
Baseline commit: content of debug-artifacts/managed-agent-preflight/feature-base.txt
Modified commit: output of git rev-parse HEAD
Baseline command, literal output, exit status
Modified command, literal output, exit status
Loopback config path and SHA-256 (no token or credential)
Managed agent log path and SHA-256
All new/modified paths
```

Redact all token/credential values; paste literal test output rather than summarizing it.

- [ ] **Step 8: Add and execute rollback**

`scripts/rollback-managed-agent.ps1` applies the recorded patch in reverse and then verifies legacy behavior:

```powershell
param()
$patch = Join-Path $PSScriptRoot '..\debug-artifacts\managed-agent.patch'
git apply --reverse --check $patch
if ($LASTEXITCODE -ne 0) { throw 'Rollback preflight failed' }
git apply --reverse $patch
if ($LASTEXITCODE -ne 0) { throw 'Rollback apply failed' }
.\.venv\Scripts\python.exe -m pytest tests/test_protocol_auth.py tests/test_c2_coverage.py -q
if ($LASTEXITCODE -ne 0) { throw 'Legacy verification failed after rollback' }
```

Verify it on a temporary branch/worktree so the completed feature branch remains intact. Confirm legacy listener tests pass and managed files are absent/disabled there.

- [ ] **Step 9: Commit verification artifacts**

```powershell
git add tests/test_agent_runtime_integration.py docs/runbooks/managed-agent-phase1.md scripts/rollback-managed-agent.ps1 debug-artifacts/managed-agent.patch debug-artifacts/managed-agent-verification.md
git diff --cached --check
git commit -m "test: verify managed agent phase one"
```

- [ ] **Step 10: Final review against the approved spec**

```powershell
git diff 9207a55..HEAD --check
git log --oneline 9207a55..HEAD
git status --short
```

Expected: no diff errors; commits correspond to Tasks 1-7; remaining worktree entries are only the preserved pre-existing paths recorded in the preflight snapshot.
