# PhantomLink Phase 2 Private-Network Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Windows-managed PhantomLink channel that enrolls agent-generated certificates, maintains heartbeat-only mTLS sessions over an externally supplied private network, persists device/audit state in SQLite, and exposes status, disconnect, and revoke in the existing Textual dashboard without adding remote commands.

**Architecture:** Keep the legacy protocol and legacy dashboard data path unchanged. Replace the Phase 1 shared-secret managed path with four focused units: a stdlib SQLite repository, a controller CA and agent certificate store using the existing DPAPI/ACL helpers, an mTLS session manager plus device services, and a Managed Agents Textual view composed beside the legacy view. The controller is the composition root; SQLite is durable state and live sockets stay in memory.

**Tech Stack:** Python 3.12, stdlib `sqlite3`/`ssl`/`socket`/`threading`/`ipaddress`, cryptography 41, pywin32 306 on Windows, Textual already used by the project, pytest 9, PowerShell.

## Global Constraints

- Approved source of truth: `docs/superpowers/specs/2026-08-13-phantomlink-phase2-private-network-dashboard-design.md` at commit `af64499`.
- Scope is only status, heartbeat, disconnect, and revoke. There is no managed command or payload message type.
- The VPN is external. No PhantomLink code installs, configures, starts, or changes a VPN.
- Production binding requires one literal IP address. Reject `0.0.0.0`, `::`, multicast, limited broadcast, and loopback; loopback is accepted only when `allow_loopback=True` is passed explicitly by a test.
- Managed and enrollment listeners bind the same validated address on separate configured ports.
- TLS minimum is 1.2. Managed sessions require a controller-trusted client certificate. Enrollment remains server-pinned HTTPS.
- Enrollment tokens default to 600 seconds, are single-use, and are stored only as SHA-256 digests.
- Agent certificates are valid for 90 days and renewal begins at 30 days remaining.
- Heartbeat defaults remain controller PING every 30 seconds, PONG timeout 10 seconds, and agent read deadline 90 seconds.
- Agent retry remains bounded: base 1 second, cap 30 seconds, jitter 20 percent.
- One live session is published per agent ID. A newer valid session replaces and closes the previous session.
- SQLite uses WAL, foreign keys, one connection per operation, explicit transactions, and a bounded 5-second busy timeout.
- SQLite contains public certificate metadata only. The controller CA private key remains in a DPAPI-protected private-ACL file outside the database.
- Audit `details_json` accepts only allowlisted scalar fields and is capped at 4096 UTF-8 bytes. Tokens, private keys, certificate bundles, DPAPI blobs, and credential material are rejected.
- Revoke is permanent and idempotent in Phase 2. Re-enrollment creates a new agent ID.
- Registry failure fails closed for authentication, enrollment, renewal, disconnect, and revoke. Display may use only a labeled last-known snapshot.
- Keep `DeviceCredential` and the Phase 1 JSON/DPAPI readers only long enough to detect and back up Phase 1 stores; no Phase 1 shared secret is converted into a certificate.
- Preserve the legacy client, listener, protocol, and dashboard tab with regression tests.
- Add no dependency. Use installed `cryptography`, `pywin32`, Textual, and the Python standard library.
- Do not delete Phase 1 specs, plans, runbooks, verification files, patches, or rollback scripts. Delete a superseded file only with a grep/import proof and a retained replacement named in the same commit.
- Preserve the existing untracked `debug-artifacts/managed-agent-preflight/`, `debug-artifacts/task4-cli-store/`, and `debug-artifacts/task4-current.diff` paths.
- Every task starts from a clean tracked diff, stages only its listed paths, runs `git diff --cached --check`, and ends in an independently reviewable commit.
- Every architecture or scope change is appended to `docs/superpowers/decisions/2026-08-13-phantomlink-phase2-decisions.md`; routine progress is recorded by tests, commits, and the final verification record.

---

## File Map

| Path | Responsibility |
|---|---|
| `C2/managed_registry.py` | Immutable display models, SQLite schema/migrations, tokens, devices, audit events, and Phase 1 store backup manifest |
| `C2/managed_pki.py` | DPAPI-protected controller CA, CSR validation, 90-day certificate signing, renewal validation, and certificate identity parsing |
| `C2/managed_services.py` | In-memory single-session manager and concrete query/action services for status, disconnect, and revoke |
| `C2/managed_auth.py` | Framing, bounded listeners/workers, pinned enrollment HTTP handler, and mTLS managed handshake/heartbeat loop |
| `client/managed_identity.py` | Agent EC private-key generation, CSR creation, DPAPI-protected certificate bundle, and temporary private-ACL TLS material loading |
| `client/agent_config.py` | Existing validated timings plus display name, version, and certificate bundle path |
| `client/agent_runtime.py` | mTLS connector, certificate renewal trigger, heartbeat response, retry, socket ownership, and shutdown |
| `client/managed_agent.py` | `enroll` and `run` composition using the Phase 2 certificate store |
| `C2/managed_dashboard.py` | Managed snapshot cache, degraded-state model, Textual table/detail/audit widgets, and confirmation dialogs |
| `C2/dashboard.py` | Compose Legacy and Managed tabs while preserving the existing legacy data adapter |
| `config.py` | Exact managed bind, registry path, CA certificate path, CA key path, and existing ports/server certificate paths |
| `C2/C2.py` | Build the Phase 2 repository, PKI, services, listeners, and dashboard; bounded startup and shutdown |
| `.env.example` | Non-secret Phase 2 controller configuration names and explicit private-network warning |
| `tests/test_managed_registry.py` | Schema, transaction, migration, redaction, and concurrent token/audit tests |
| `tests/test_managed_pki.py` | CA storage, CSR signing, identity, expiry, renewal, and failure tests |
| `tests/test_managed_identity.py` | Agent private-key locality, DPAPI round trip, CSR, and TLS context tests |
| `tests/test_managed_services.py` | State derivation, session replacement, disconnect/revoke semantics, races, and degraded queries |
| `tests/test_managed_auth.py` | Enrollment v2 handler and mTLS listener unit/security tests |
| `tests/test_agent_runtime.py` | mTLS connector, renewal threshold, heartbeat, retry, and stop tests |
| `tests/test_managed_dashboard.py` | Managed data cache, filters, dialogs, keyboard actions, accessibility text, and degraded mode |
| `tests/test_phase2_integration.py` | Real ephemeral CA/cert/SQLite/TCP end-to-end behavior |
| `tests/test_dashboard.py` | Legacy dashboard regression plus dual-tab composition smoke |
| `tests/test_agent_runtime_integration.py` | Existing Phase 1 regression retained until final replacement evidence is recorded |
| `docs/runbooks/managed-agent-phase2-private-network.md` | Exact setup, enrollment, run, renewal, disconnect, revoke, two-machine acceptance, and recovery commands |
| `docs/superpowers/decisions/2026-08-13-phantomlink-phase2-decisions.md` | Durable approved decisions and known ceilings |
| `scripts/rollback-managed-agent-phase2.ps1` | Verified reverse application of the exact Phase 2 patch |
| `debug-artifacts/managed-agent-phase2.patch` | Exact reversible Phase 2 delta from `af64499` excluding generated evidence files |
| `debug-artifacts/managed-agent-phase2-verification.md` | Literal baseline/modified/rollback commands, inputs, outputs, and exit statuses |

---

### Task 1: Durable SQLite registry and Phase 1 store preservation

**Files:**
- Create: `C2/managed_registry.py`
- Create: `tests/test_managed_registry.py`
- Modify: `C2/managed_auth.py:155-564,1018-1053`
- Modify: `docs/superpowers/decisions/2026-08-13-phantomlink-phase2-decisions.md`

**Interfaces:**
- Produces immutable `DeviceSummary(agent_id: str, display_name: str, state: str, last_vpn_ip: str | None, last_seen_at: str | None, certificate_not_after: str, agent_version: str)`, `DeviceDetail(agent_id: str, display_name: str, state: str, last_vpn_ip: str | None, last_seen_at: str | None, certificate_not_after: str, agent_version: str, certificate_fingerprint: str, certificate_serial: str, enrolled_at: str, revoked_at: str | None, revocation_reason: str | None)`, `AuditEvent(id: int, occurred_at: str, actor: str, action: str, target_agent_id: str | None, result: str, reason: str | None, correlation_id: str, details: tuple[tuple[str, str], ...])`, `ActionResult(code: str, message: str, correlation_id: str)`, and `LegacyStoreBackup(source_name: str, backup_path: Path, byte_count: int, sha256: str)` dataclasses.
- Produces `ManagedRegistry(path: Path, *, now: Callable[[], datetime] = utc_now, busy_timeout_ms: int = 5000)`.
- Produces immutable `IssuedDeviceCertificate(certificate_pem: bytes, fingerprint: str, serial: str, certificate_not_after: str)`.
- Produces `initialize()`, `issue_token(ttl_seconds: float = 600.0) -> str`, `consume_token_and_enroll(token: str, certificate: IssuedDeviceCertificate, display_name: str, agent_version: str, actor: str, correlation_id: str) -> DeviceDetail`, `renew_certificate(agent_id: str, current_fingerprint: str, certificate: IssuedDeviceCertificate, actor: str, correlation_id: str) -> DeviceDetail`, `get_device(agent_id: str) -> DeviceDetail | None`, `list_device_records() -> tuple[DeviceDetail, ...]`, `list_audit_events(limit: int = 100) -> tuple[AuditEvent, ...]`, `append_audit(*, actor: str, action: str, target_agent_id: str | None, result: str, reason: str | None, correlation_id: str, details: Mapping[str, str | int | float | bool | None]) -> AuditEvent`, `revoke_device(agent_id: str, actor: str, reason: str, correlation_id: str) -> ActionResult`, `touch_last_seen(agent_id: str, vpn_ip: str, occurred_at: datetime | None = None) -> None`, and `is_connection_allowed(agent_id: str, fingerprint: str, serial: str) -> bool`.
- Produces `backup_phase1_stores(store_root: Path, backup_root: Path, *, now: Callable[[], datetime] = utc_now) -> tuple[LegacyStoreBackup, ...]`.
- `IssuedDeviceCertificate` is declared in this file as public certificate metadata only so Task 2 can return it without a repository/PKI import cycle.

- [ ] **Step 1: Capture the starting state and write failing schema/model tests**

```powershell
New-Item -ItemType Directory -Force debug-artifacts/managed-agent-phase2-preflight | Out-Null
git status --short | Out-File -Encoding utf8NoBOM debug-artifacts/managed-agent-phase2-preflight/status.txt
git diff --binary | Out-File -Encoding utf8NoBOM debug-artifacts/managed-agent-phase2-preflight/worktree.patch
git rev-parse HEAD | Out-File -Encoding ascii debug-artifacts/managed-agent-phase2-preflight/base.txt
git diff --check
```

Add these first tests:

```python
def test_initialize_creates_exact_version_one_schema(tmp_path):
    registry = ManagedRegistry(tmp_path / "managed.db")
    registry.initialize()
    with registry._connection() as connection:
        tables = {
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert {"schema_version", "devices", "enrollment_tokens", "audit_events"} <= tables
        assert connection.execute("SELECT version FROM schema_version").fetchall() == [(1,)]
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_display_models_are_frozen():
    field_names = tuple(field.name for field in fields(DeviceSummary))
    assert field_names == (
        "agent_id", "display_name", "state", "last_vpn_ip", "last_seen_at",
        "certificate_not_after", "agent_version",
    )
    with pytest.raises(FrozenInstanceError):
        DeviceSummary("a", "pc", "ENROLLED", None, None, "2026-01-01T00:00:00Z", "2").state = "ONLINE"
```

- [ ] **Step 2: Run the focused tests and verify the missing module failure**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_managed_registry.py -q
```

Expected: collection exits nonzero with `ModuleNotFoundError: No module named 'C2.managed_registry'`.

- [ ] **Step 3: Add the exact schema and connection boundary**

Create `C2/managed_registry.py` with the four tables and indexes copied from Design sections 6 and 7. Use this connection boundary for every operation:

```python
@contextmanager
def _connection(self):
    connection = sqlite3.connect(
        self.path,
        timeout=self.busy_timeout_ms / 1000,
        isolation_level=None,
    )
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms}")
        yield connection
    finally:
        connection.close()
```

`initialize()` creates the parent directory, applies its private ACL through the existing `_apply_private_acl`, executes `PRAGMA journal_mode=WAL`, runs `BEGIN IMMEDIATE`, installs schema version 1, commits, and reapplies the ACL to the database plus existing `-wal` and `-shm` files. Reject a database whose highest schema version is greater than 1.

- [ ] **Step 4: Write failing token, enrollment, audit, and redaction tests**

```python
def test_token_is_atomic_single_use_across_registry_instances(tmp_path, issued_certificate):
    path = tmp_path / "managed.db"
    first = ManagedRegistry(path)
    second = ManagedRegistry(path)
    first.initialize()
    token = first.issue_token(600)
    barrier = threading.Barrier(2)
    outcomes = []

    def consume(registry):
        barrier.wait()
        try:
            outcomes.append(registry.consume_token_and_enroll(
                token, issued_certificate, "pc-01", "2.0", "enrollment", "corr-1"
            ).agent_id)
        except EnrollmentTokenRejected:
            outcomes.append("rejected")

    threads = [threading.Thread(target=consume, args=(registry,)) for registry in (first, second)]
    [thread.start() for thread in threads]
    [thread.join(2) for thread in threads]
    assert len([value for value in outcomes if value != "rejected"]) == 1
    assert outcomes.count("rejected") == 1


@pytest.mark.parametrize("forbidden", ["token", "private_key", "certificate_bundle", "dpapi_blob", "secret"])
def test_audit_details_reject_credential_fields(registry, forbidden):
    with pytest.raises(ValueError, match="forbidden audit detail"):
        registry.append_audit(
            actor="test", action="TEST", target_agent_id=None, result="FAILED",
            reason=None, correlation_id="corr-1", details={forbidden: "value"},
        )
```

- [ ] **Step 5: Implement atomic repository operations and stable validation**

Use `BEGIN IMMEDIATE` for token consumption/enrollment, renewal, and revoke. Store times as UTC RFC3339 strings ending in `Z`. Canonicalize audit JSON with `sort_keys=True` and `separators=(",", ":")`; require a mapping whose keys are in this exact set:

```python
AUDIT_DETAIL_KEYS = frozenset({
    "agent_version", "certificate_fingerprint", "certificate_serial",
    "peer_ip", "previous_session_id", "session_id", "status_code",
})
AUDIT_DETAIL_LIMIT = 4096
```

Token validation reuses the existing canonical 32-byte URL-safe format and `hashlib.sha256(token.encode("ascii")).hexdigest()`. Never return or log the digest. `consume_token_and_enroll` verifies an unconsumed future expiry inside the transaction, inserts the device and `ENROLLMENT_SUCCEEDED` audit event, marks `consumed_at`, then commits.

- [ ] **Step 6: Add and prove non-converting Phase 1 backups**

```python
def test_phase1_backup_hashes_bytes_without_importing_credentials(tmp_path):
    store = tmp_path / "managed-store"
    store.mkdir()
    (store / "tokens.json").write_bytes(b'{"legacy":true}')
    (store / "devices.bin").write_bytes(b"protected-secret-data")
    backups = backup_phase1_stores(store, tmp_path / "backup", now=fixed_now)
    assert {item.source_name for item in backups} == {"tokens.json", "devices.bin"}
    assert all(item.sha256 == hashlib.sha256(item.backup_path.read_bytes()).hexdigest() for item in backups)
    assert not (tmp_path / "managed-store" / "managed.db").exists()
    assert b"protected-secret-data" not in (tmp_path / "backup" / "manifest.json").read_bytes()
```

`backup_phase1_stores` copies bytes with `shutil.copyfile`, verifies source/destination SHA-256 equality, writes a canonical manifest containing source name, backup relative path, byte count, and digest, and reapplies the private ACL. It never calls `DeviceRegistry`, decrypts `devices.bin`, or deletes either source.

- [ ] **Step 7: Retire runtime use of Phase 1 JSON stores and run the registry suite**

Keep framing and listener code in `C2/managed_auth.py`, but replace `_store_services()` and CLI storage calls with `ManagedRegistry(store / "managed.db")`. Retain the old reader classes only until Task 8 confirms no production import; mark their removal for Task 8 rather than deleting evidence now.

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_managed_registry.py tests/test_managed_auth.py -q
.\.venv\Scripts\python.exe -m pytest tests/test_agent_runtime_integration.py -q
```

Expected: all tests pass; the existing integration suite remains green.

- [ ] **Step 8: Commit the registry slice**

```powershell
git add C2/managed_registry.py C2/managed_auth.py tests/test_managed_registry.py docs/superpowers/decisions/2026-08-13-phantomlink-phase2-decisions.md
git diff --cached --check
git diff --cached --name-only
git commit -m "feat: add durable managed registry"
```

Expected staged paths: exactly the four paths above.

---

### Task 2: Controller CA and agent certificate identity

**Files:**
- Create: `C2/managed_pki.py`
- Create: `client/managed_identity.py`
- Create: `tests/test_managed_pki.py`
- Create: `tests/test_managed_identity.py`
- Modify: `client/agent_config.py:21-85,298-385`
- Test: `tests/test_agent_config.py`

**Interfaces:**
- Consumes `IssuedDeviceCertificate` and existing `_DpapiProtector`, `_atomic_private_write`, `_read_private_file`, `_apply_private_acl`.
- Produces `ControllerCertificateAuthority(key_path: Path, certificate_path: Path, *, protector=None, now=utc_now)` with `initialize(common_name: str) -> None`, `sign_device_csr(csr_pem: bytes, agent_id: str) -> IssuedDeviceCertificate`, `renew_device_csr(csr_pem: bytes, agent_id: str) -> IssuedDeviceCertificate`, and `ca_pem() -> bytes`.
- Produces frozen `AgentCertificateIdentity(agent_id: str, certificate_pem: bytes, chain_pem: bytes, private_key_pem: bytes, certificate_serial: str, certificate_not_after: str)`.
- Produces `AgentCertificateStore(path: Path, *, protector=None, acl_inspector=None, acl_applier=None)` with `create_csr(display_name: str) -> tuple[bytes, bytes]`, `save_enrollment(private_key_pem: bytes, *, agent_id: str, certificate_pem: bytes, chain_pem: bytes, certificate_serial: str, certificate_not_after: str) -> AgentCertificateIdentity`, `load() -> AgentCertificateIdentity | None`, `delete() -> None`, and `client_context(identity: AgentCertificateIdentity) -> ssl.SSLContext`.
- Produces `build_enrollment_request(token: str, display_name: str, agent_version: str, csr_pem: bytes) -> bytes`.

- [ ] **Step 1: Write failing CA and certificate-profile tests**

```python
def test_ca_signs_90_day_client_certificate_with_agent_uri(tmp_path, fake_protector, fixed_now):
    ca = ControllerCertificateAuthority(
        tmp_path / "ca-key.dpapi", tmp_path / "ca.pem",
        protector=fake_protector, now=lambda: fixed_now,
    )
    ca.initialize("PhantomLink Test CA")
    private_key, csr = make_agent_csr("pc-01")
    issued = ca.sign_device_csr(csr, "11111111-1111-4111-8111-111111111111")
    certificate = x509.load_pem_x509_certificate(issued.certificate_pem)
    assert certificate.not_valid_after_utc - certificate.not_valid_before_utc == timedelta(days=90)
    assert ExtendedKeyUsageOID.CLIENT_AUTH in certificate.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    assert "urn:phantomlink:agent:11111111-1111-4111-8111-111111111111" in {
        value.value for value in certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    }
    assert private_key.private_numbers().private_value > 0
```

Also assert the CA key file contains protected bytes, the SQLite fixture contains none of the key bytes, invalid CSR signatures fail, non-EC-P256 public keys fail, invalid UUID agent IDs fail, signing fails closed when the protected key is missing, and the CA certificate is valid for CA signing only.

- [ ] **Step 2: Run the focused tests and verify both modules are missing**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_managed_pki.py tests/test_managed_identity.py -q
```

Expected: collection exits nonzero because `C2.managed_pki` and `client.managed_identity` do not exist.

- [ ] **Step 3: Implement the minimum controller CA**

Use `ec.generate_private_key(ec.SECP256R1())`, SHA-256 signatures, random 128-bit positive serials from `x509.random_serial_number()`, and these certificate extensions:

```python
DEVICE_URI_PREFIX = "urn:phantomlink:agent:"
CERTIFICATE_LIFETIME = timedelta(days=90)
RENEWAL_WINDOW = timedelta(days=30)
```

The device certificate has `BasicConstraints(ca=False)`, `KeyUsage(digital_signature=True, key_encipherment=False, key_cert_sign=False, crl_sign=False, content_commitment=False, data_encipherment=False, key_agreement=False, encipher_only=False, decipher_only=False)`, `ExtendedKeyUsage([CLIENT_AUTH])`, subject/authority key identifiers, and one URI SAN containing the assigned UUID. Reject CSR subject fields other than a bounded common name and copy no unvalidated CSR extension into the signed certificate.

Serialize the CA private key as unencrypted PKCS8 only in memory, immediately protect it through `_DpapiProtector`, and atomically write the protected blob with the existing private ACL helper. Write only the public CA certificate as PEM.

- [ ] **Step 4: Write failing agent-store and private-key-locality tests**

```python
def test_agent_store_keeps_private_key_out_of_enrollment_request(tmp_path, fake_protector):
    store = AgentCertificateStore(tmp_path / "identity.dpapi", protector=fake_protector)
    private_key_pem, csr_pem = store.create_csr("pc-01")
    request = build_enrollment_request("token-value", "pc-01", "2.0", csr_pem)
    assert private_key_pem.startswith(b"-----BEGIN PRIVATE KEY-----")
    assert private_key_pem not in request
    assert b"PRIVATE KEY" not in request
    assert b"CERTIFICATE REQUEST" in request


def test_client_context_loads_cert_chain_then_removes_temporary_files(identity_store, issued_identity, monkeypatch):
    observed = []
    monkeypatch.setattr(identity_store, "_after_load", lambda paths: observed.extend(paths))
    context = identity_store.client_context(issued_identity)
    assert context.minimum_version == ssl.TLSVersion.TLSv1_2
    assert observed and all(not path.exists() for path in observed)
```

- [ ] **Step 5: Implement the DPAPI certificate bundle and config additions**

`AgentCertificateStore.save_enrollment` verifies that the returned certificate public key matches the generated private key, validates the CA signature, checks the exact URI agent ID, and only then writes a canonical JSON bundle protected by DPAPI. The protected bundle contains base64 PEM values and public metadata; the adjacent JSON config contains none of them.

To call `SSLContext.load_cert_chain`, create a private-ACL temporary directory under the identity file's parent, write certificate and key files with `_atomic_private_write`, load them, and remove both files plus the directory in `finally`. Set TLS 1.2 minimum, `check_hostname=False`, `verify_mode=CERT_REQUIRED`, and trust only the stored CA PEM; controller identity remains pinned by the existing SHA-256 leaf pin check after handshake.

Add these optional `AgentConfig` fields while preserving every existing Phase 1 field:

```python
display_name: str = ""
agent_version: str = "2.0"
certificate_store_path: str = "managed-identity.dpapi"
```

Validate display name as 1 to 128 printable characters after defaulting empty to `socket.gethostname()` in the composition root, validate version as 1 to 32 visible ASCII characters, and require a non-empty certificate store path.

- [ ] **Step 6: Run certificate, config, and existing ACL tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_managed_pki.py tests/test_managed_identity.py tests/test_agent_config.py -q
.\.venv\Scripts\python.exe -m pytest tests/test_phantomlink_config_import.py -q
```

Expected: all tests pass and importing the identity module does not import `client.PhantomLink`.

- [ ] **Step 7: Commit the PKI slice**

```powershell
git add C2/managed_pki.py client/managed_identity.py client/agent_config.py tests/test_managed_pki.py tests/test_managed_identity.py tests/test_agent_config.py
git diff --cached --check
git diff --cached --name-only
git commit -m "feat: add managed certificate identities"
```

Expected staged paths: exactly the six paths above.

---

### Task 3: Pinned enrollment and certificate renewal

**Files:**
- Modify: `C2/managed_auth.py:497-564,920-1016,1018-1053`
- Modify: `client/managed_agent.py:31-132,158-285`
- Modify: `client/agent_runtime.py:109-244,248-445`
- Modify: `tests/test_managed_auth.py`
- Modify: `tests/test_agent_runtime.py`
- Test: `tests/test_managed_registry.py`
- Test: `tests/test_managed_pki.py`
- Test: `tests/test_managed_identity.py`

**Interfaces:**
- Consumes `ManagedRegistry`, `ControllerCertificateAuthority`, `AgentCertificateStore`, and `IssuedDeviceCertificate`.
- Produces `EnrollmentService(registry: ManagedRegistry, certificate_authority: ControllerCertificateAuthority, *, now=utc_now)` with `exchange(token: str, csr_pem: bytes, display_name: str, agent_version: str) -> EnrollmentResponse` and `renew(agent_id: str, fingerprint: str, csr_pem: bytes) -> EnrollmentResponse`.
- Produces frozen `EnrollmentResponse(agent_id: str, certificate_pem: bytes, chain_pem: bytes, certificate_serial: str, certificate_not_after: str)`.
- Changes `enroll(config: AgentConfig, token: str, store: AgentCertificateStore) -> AgentCertificateIdentity` and adds `renew(config: AgentConfig, identity: AgentCertificateIdentity, store: AgentCertificateStore) -> AgentCertificateIdentity`.

- [ ] **Step 1: Replace shared-secret enrollment assertions with CSR assertions**

```python
def test_enrollment_consumes_token_and_returns_only_public_certificate_material(registry, ca, csr_pem):
    token = registry.issue_token(600)
    service = EnrollmentService(registry, ca)
    response = service.exchange(token, csr_pem, "pc-01", "2.0")
    assert response.agent_id
    assert response.certificate_pem.startswith(b"-----BEGIN CERTIFICATE-----")
    assert response.chain_pem.startswith(b"-----BEGIN CERTIFICATE-----")
    assert not hasattr(response, "secret")
    with pytest.raises(EnrollmentTokenRejected):
        service.exchange(token, csr_pem, "pc-01", "2.0")


def test_signing_failure_does_not_consume_token(registry, failing_ca, csr_pem):
    token = registry.issue_token(600)
    service = EnrollmentService(registry, failing_ca)
    with pytest.raises(SigningUnavailable):
        service.exchange(token, csr_pem, "pc-01", "2.0")
    digest = hashlib.sha256(token.encode("ascii")).hexdigest()
    with registry._connection() as connection:
        row = connection.execute(
            "SELECT consumed_at FROM enrollment_tokens WHERE token_digest = ?", (digest,)
        ).fetchone()
    assert row["consumed_at"] is None
```

The token probe uses the private test connection and a direct SQL query; do not add it to the production repository API.

- [ ] **Step 2: Run the focused tests and verify the Phase 1 response shape fails**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_managed_auth.py -q
```

Expected: failures show the current response contains `key_id`/`secret` and accepts no CSR.

- [ ] **Step 3: Implement enrollment with sign-before-commit ordering**

The handler accepts exactly this bounded JSON request:

```json
{"agent_version":"2.0","csr_pem":"<base64 DER CSR>","display_name":"pc-01","token":"<43 character one-time token>"}
```

The implementation decodes at most 64 KiB, validates all fields, signs the CSR in memory, then calls the single repository transaction that consumes the token, inserts public device metadata, and appends `ENROLLMENT_SUCCEEDED`. It returns HTTP 201 with agent ID, base64 certificate PEM, base64 CA chain PEM, serial, and expiry. Use only stable external failures: HTTP 400 malformed request, 403 invalid/expired/consumed token, 503 signer or registry unavailable. Log only the stable event code and correlation ID.

Configure the enrollment HTTPS context with the controller CA and `verify_mode=ssl.CERT_OPTIONAL`: `/v1/enroll` accepts no client certificate and relies on the pinned server leaf plus one-time token, while `/v1/renew` requires and validates the presented current client certificate before reading the CSR. An untrusted client certificate fails the TLS handshake; an absent certificate on `/v1/renew` returns 403.

- [ ] **Step 4: Add renewal tests for the 30-day threshold and revoked rejection**

```python
def test_runtime_renews_only_at_30_days_or_less(valid_identity, config, store, connector, renewer):
    runtime = AgentRuntime(config, valid_identity, identity_store=store, connector=connector, renewer=renewer)
    runtime.prepare_identity(now=parse_time(valid_identity.certificate_not_after) - timedelta(days=31))
    assert renewer.calls == []
    runtime.prepare_identity(now=parse_time(valid_identity.certificate_not_after) - timedelta(days=30))
    assert renewer.calls == [valid_identity.agent_id]


def test_renewal_rejects_revoked_device(registry, enrolled_device, service, csr_pem):
    registry.revoke_device(enrolled_device.agent_id, "operator", "retired", "corr-r")
    with pytest.raises(CertificateRejected):
        service.renew(enrolled_device.agent_id, enrolled_device.certificate_fingerprint, csr_pem)
```

- [ ] **Step 5: Implement renewal and atomic agent identity replacement**

Renewal uses the current mTLS peer identity, not a bearer token. It accepts a fresh CSR, rechecks the current fingerprint and non-revoked registry row, signs a 90-day certificate with the same agent ID, and commits certificate metadata plus `CERTIFICATE_RENEWED` in one transaction. The agent verifies the response and atomically replaces the DPAPI bundle only after all checks pass. A failed save leaves the previous valid bundle untouched.

In `AgentRuntime.prepare_identity`, renew synchronously before connecting only when remaining validity is at most 30 days; if renewal fails but the current certificate remains valid, emit `CERTIFICATE_RENEWAL_FAILED` and continue bounded reconnect. If expired, emit `CERTIFICATE_EXPIRED` and stop retrying.

- [ ] **Step 6: Update the CLI token/store flow and run focused tests**

`client.managed_agent enroll` keeps `--token-file`, generates the key/CSR locally, posts the CSR, verifies and saves the bundle, then writes only `agent_id` into the non-secret config identity fields. `run` loads `AgentCertificateStore` rather than `DpapiCredentialStore`. Keep exit codes 0 success, 2 invocation/input, 3 enrollment required, 4 invalid config, and 5 platform/auth/storage failure.

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_managed_auth.py tests/test_agent_runtime.py tests/test_managed_identity.py tests/test_managed_registry.py tests/test_managed_pki.py -q
.\.venv\Scripts\python.exe -m pytest tests/test_agent_runtime_integration.py -q
```

Expected: all tests pass; no test or response searches find the old managed `secret` field outside retained Phase 1 evidence.

- [ ] **Step 7: Commit the enrollment slice**

```powershell
git add C2/managed_auth.py client/managed_agent.py client/agent_runtime.py tests/test_managed_auth.py tests/test_agent_runtime.py
git diff --cached --check
git diff --cached --name-only
git commit -m "feat: enroll managed certificates"
```

Expected staged paths: exactly the five paths above.

---

### Task 4: Exact private-network binding and mTLS heartbeat sessions

**Files:**
- Create: `C2/managed_services.py`
- Create: `tests/test_managed_services.py`
- Modify: `C2/managed_auth.py:565-920`
- Modify: `client/agent_runtime.py:64-445`
- Modify: `tests/test_managed_auth.py`
- Modify: `tests/test_agent_runtime.py`

**Interfaces:**
- Consumes `ManagedRegistry.is_connection_allowed`, certificate URI identities from `C2.managed_pki`, and existing JSON framing.
- Produces `validate_managed_bind(host: str, *, allow_loopback: bool = False) -> str`.
- Produces frozen `SessionSnapshot(agent_id: str, session_id: str, peer_ip: str, connected_at: str, last_heartbeat_at: str)`.
- Produces `SessionManager(registry: ManagedRegistry, *, now=utc_now)` with `register(agent_id: str, fingerprint: str, serial: str, peer_ip: str, connection: socket.socket) -> SessionSnapshot`, `heartbeat(agent_id: str, session_id: str) -> None`, `unregister(agent_id: str, session_id: str, reason: str) -> bool`, `disconnect(agent_id: str) -> bool`, `snapshot() -> tuple[SessionSnapshot, ...]`, and `close_all() -> None`.
- Produces `ManagedServer(host: str, port: int, certfile: Path, keyfile: Path, ca_certfile: Path, registry: ManagedRegistry, sessions: SessionManager, *, allow_loopback: bool = False, max_workers: int = 32, handshake_timeout: float = 5.0, ping_interval: float = 30.0, pong_timeout: float = 10.0)` using TLS client-certificate verification and no application shared-secret handshake.

- [ ] **Step 1: Write failing bind and mTLS-context tests**

```python
@pytest.mark.parametrize("host", ["0.0.0.0", "::", "224.0.0.1", "ff02::1", "255.255.255.255", "8.8.8.8", "localhost", "vpn.example"])
def test_production_bind_rejects_non_exact_private_interface(host):
    with pytest.raises(ValueError, match="exact managed IP"):
        validate_managed_bind(host)


def test_loopback_requires_explicit_test_flag():
    with pytest.raises(ValueError):
        validate_managed_bind("127.0.0.1")
    assert validate_managed_bind("127.0.0.1", allow_loopback=True) == "127.0.0.1"


def test_managed_server_context_requires_client_certificate(server_context):
    assert server_context.minimum_version == ssl.TLSVersion.TLSv1_2
    assert server_context.verify_mode == ssl.CERT_REQUIRED
```

- [ ] **Step 2: Run focused tests and verify current permissive TLS fails**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_managed_auth.py -q
```

Expected: tests fail because `_server_context` does not load the CA or require client certificates and no bind validator exists.

- [ ] **Step 3: Implement exact bind validation and mTLS identity extraction**

Use `ipaddress.ip_address(host)`; reject parse failures, globally routable, unspecified, multicast, link-local, `255.255.255.255`, and loopback unless the explicit test flag is true. Do not accept DNS names. This accepts RFC1918, unique-local IPv6, and shared-address VPN ranges such as `100.64.0.0/10`; let the OS bind call prove the literal address exists on a local interface.

The server context loads the controller server certificate/key, trusts only `ControllerCertificateAuthority.ca_pem()`, sets `verify_mode=CERT_REQUIRED`, disables TLS compression where supported, and sets TLS 1.2 minimum. After TLS handshake, parse the peer DER certificate, require exactly one `urn:phantomlink:agent:<UUID>` URI SAN, calculate SHA-256 fingerprint and decimal serial, then call `registry.is_connection_allowed`. Remove HELLO/CHALLENGE/AUTH_PROOF from the managed session; the first application frame is controller `PING`.

- [ ] **Step 4: Write failing replacement, timeout, and stale-unregister tests**

```python
def test_new_session_atomically_replaces_old_and_stale_unregister_cannot_remove_new(registry, enrolled_device):
    sessions = SessionManager(registry)
    old_conn, new_conn = FakeConnection(), FakeConnection()
    old = sessions.register(enrolled_device.agent_id, enrolled_device.certificate_fingerprint, enrolled_device.certificate_serial, "10.8.0.21", old_conn)
    new = sessions.register(enrolled_device.agent_id, enrolled_device.certificate_fingerprint, enrolled_device.certificate_serial, "10.8.0.21", new_conn)
    assert old_conn.closed is True
    assert sessions.unregister(enrolled_device.agent_id, old.session_id, "peer_closed") is False
    assert sessions.snapshot()[0].session_id == new.session_id


def test_heartbeat_timeout_removes_session_and_audits(registry, managed_server, enrolled_device):
    session = managed_server.sessions.register(enrolled_device.agent_id, enrolled_device.certificate_fingerprint, enrolled_device.certificate_serial, "10.8.0.21", FakeConnection())
    managed_server.expire_session_for_test(session)
    assert managed_server.sessions.snapshot() == ()
    assert registry.list_audit_events(1)[0].action == "HEARTBEAT_TIMEOUT"
```

- [ ] **Step 5: Implement bounded single-session publication and heartbeat**

`SessionManager.register` holds one `threading.RLock`, rechecks the durable row immediately before replacing `_sessions[agent_id]`, appends `SESSION_REPLACED` or `CONNECTED`, and releases the lock before closing the previous socket. `unregister` compares session IDs so an old worker cannot erase a newer session. `disconnect` removes the current record under the same lock and closes outside it. All closes use `shutdown(SHUT_RDWR)` followed by `close()` with `OSError` suppressed.

Keep the existing bounded worker semaphore and non-daemon worker tracking. Reject saturation before TLS work. The managed loop sends PING every 30 seconds, requires PONG within 10 seconds, updates `last_seen_at` and the in-memory timestamp, and audits timeout/disconnect with stable codes. It never accepts any frame other than `PONG`.

- [ ] **Step 6: Convert the agent connector to mTLS and remove shared-secret authentication**

`ManagedConnector.connect(config, identity)` obtains the preconfigured context from `AgentCertificateStore.client_context`, performs TLS, compares the server leaf SHA-256 pin with `config.tls_cert_sha256`, then returns the socket. Delete `_authenticate`, `build_proof`, `key_id`, and secret use from the managed runtime path. Preserve socket hooks, owner-thread shutdown, deadline handling, retry classification, and PING/PONG framing.

- [ ] **Step 7: Run managed listener/runtime and legacy protocol regressions**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_managed_services.py tests/test_managed_auth.py tests/test_agent_runtime.py -q
.\.venv\Scripts\python.exe -m pytest tests/test_client_transport.py tests/test_protocol_auth.py tests/test_encryption.py -q
```

Expected: all tests pass; managed tests contain no application-layer shared-secret authentication, and legacy protocol tests remain unchanged and green.

- [ ] **Step 8: Commit the mTLS session slice**

```powershell
git add C2/managed_services.py C2/managed_auth.py client/agent_runtime.py tests/test_managed_services.py tests/test_managed_auth.py tests/test_agent_runtime.py
git diff --cached --check
git diff --cached --name-only
git commit -m "feat: run managed heartbeat over mtls"
```

Expected staged paths: exactly the six paths above.

---

### Task 5: Typed device queries, disconnect, and permanent revoke

**Files:**
- Modify: `C2/managed_services.py`
- Modify: `C2/managed_registry.py`
- Modify: `tests/test_managed_services.py`
- Modify: `tests/test_managed_registry.py`

**Interfaces:**
- Consumes `ManagedRegistry` and `SessionManager` from Tasks 1 and 4.
- Produces concrete `DeviceQueryService(registry: ManagedRegistry, sessions: SessionManager)` with `list_devices() -> tuple[DeviceSummary, ...]`, `get_device(agent_id: str) -> DeviceDetail | None`, and `list_audit_events(limit: int = 100) -> tuple[AuditEvent, ...]`.
- Produces concrete `DeviceActionService(registry: ManagedRegistry, sessions: SessionManager)` with `disconnect(agent_id: str, actor: str, reason: str) -> ActionResult` and `revoke(agent_id: str, actor: str, reason: str) -> ActionResult`.

- [ ] **Step 1: Write failing state derivation and stable result-code tests**

```python
def test_query_derives_all_four_states(registry, sessions, devices):
    never_connected, online, offline, revoked = devices
    registry.touch_last_seen(online.agent_id, "10.8.0.21")
    sessions.register(online.agent_id, online.certificate_fingerprint, online.certificate_serial, "10.8.0.21", FakeConnection())
    registry.touch_last_seen(offline.agent_id, "10.8.0.22")
    registry.revoke_device(revoked.agent_id, "operator", "retired", "corr-r")
    states = {item.agent_id: item.state for item in DeviceQueryService(registry, sessions).list_devices()}
    assert states == {
        never_connected.agent_id: "ENROLLED", online.agent_id: "ONLINE",
        offline.agent_id: "OFFLINE", revoked.agent_id: "REVOKED",
    }


def test_disconnect_commits_request_before_closing_socket(registry, sessions, enrolled_device):
    connection = AuditCheckingConnection(registry, "DISCONNECT_REQUESTED")
    sessions.register(enrolled_device.agent_id, enrolled_device.certificate_fingerprint, enrolled_device.certificate_serial, "10.8.0.21", connection)
    result = DeviceActionService(registry, sessions).disconnect(enrolled_device.agent_id, "operator", "maintenance")
    assert result.code == "DISCONNECTED"
    assert connection.closed is True
```

- [ ] **Step 2: Run focused tests and verify services are incomplete**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_managed_services.py tests/test_managed_registry.py -q
```

Expected: failures name missing `DeviceQueryService` and `DeviceActionService`.

- [ ] **Step 3: Implement query contracts and strict inputs**

Agent IDs must parse as UUIDs. Actor is 1 to 128 printable characters. Disconnect reason is optional but capped at 512 printable characters; revoke reason is required and has the same cap. Audit limit must be an integer from 1 through 1000. Return tuples sorted by case-folded display name and then agent ID. Merge durable rows with one immutable session snapshot; never expose sockets or mutable dictionaries.

If a registry read fails, raise `RegistryUnavailable` for the dashboard adapter to label. Do not synthesize an empty healthy result.

- [ ] **Step 4: Write failing revoke idempotency and race tests**

```python
def test_revoke_is_durable_idempotent_and_closes_live_session(registry, sessions, enrolled_device):
    connection = FakeConnection()
    sessions.register(enrolled_device.agent_id, enrolled_device.certificate_fingerprint, enrolled_device.certificate_serial, "10.8.0.21", connection)
    actions = DeviceActionService(registry, sessions)
    first = actions.revoke(enrolled_device.agent_id, "operator", "retired")
    second = actions.revoke(enrolled_device.agent_id, "operator", "retired")
    assert (first.code, second.code) == ("REVOKED", "ALREADY_REVOKED")
    assert connection.closed is True
    assert registry.is_connection_allowed(enrolled_device.agent_id, enrolled_device.certificate_fingerprint, enrolled_device.certificate_serial) is False


def test_revoke_wins_connect_race(registry, enrolled_device):
    sessions = PausingSessionManager(registry)
    connection = FakeConnection()
    thread = threading.Thread(target=sessions.register, args=(enrolled_device.agent_id, enrolled_device.certificate_fingerprint, enrolled_device.certificate_serial, "10.8.0.21", connection))
    thread.start()
    sessions.wait_until_registry_check_started()
    result = DeviceActionService(registry, sessions).revoke(enrolled_device.agent_id, "operator", "retired")
    sessions.release_registry_check()
    thread.join(2)
    assert result.code == "REVOKED"
    assert sessions.snapshot() == ()
```

- [ ] **Step 5: Implement audit-before-disconnect and transaction-before-revoke**

`disconnect` first verifies the durable device and appends `DISCONNECT_REQUESTED`; if that write fails, return `FAILED` and leave the socket untouched. It then calls `sessions.disconnect`, appends `DISCONNECT_SUCCEEDED` or `DISCONNECT_ALREADY_OFFLINE`, and returns `DISCONNECTED` or `ALREADY_OFFLINE` with one correlation ID.

`revoke` calls one registry transaction that sets `revoked_at`/`revocation_reason` and appends `REVOKED`, then calls `sessions.disconnect`. Repeated revoke returns `ALREADY_REVOKED` without changing the first timestamp or reason and appends an idempotent-result audit. Missing IDs return `NOT_FOUND`. There is no unrevoke method.

- [ ] **Step 6: Prove concurrency and failure ordering**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_managed_services.py tests/test_managed_registry.py -q
1..30 | ForEach-Object { .\.venv\Scripts\python.exe -m pytest tests/test_managed_services.py -q; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE } }
```

Expected: every run exits 0; audit-before-close and revoke-wins-race assertions remain deterministic.

- [ ] **Step 7: Commit the device-service slice**

```powershell
git add C2/managed_services.py C2/managed_registry.py tests/test_managed_services.py tests/test_managed_registry.py
git diff --cached --check
git diff --cached --name-only
git commit -m "feat: add managed device actions"
```

Expected staged paths: exactly the four paths above.

---

### Task 6: Managed Agents Textual tab and degraded registry mode

**Files:**
- Create: `C2/managed_dashboard.py`
- Create: `tests/test_managed_dashboard.py`
- Modify: `C2/dashboard.py:1-247`
- Modify: `tests/test_dashboard.py`

**Interfaces:**
- Consumes `DeviceQueryService`, `DeviceActionService`, immutable display models, and the existing `DashboardData` legacy adapter.
- Produces frozen `ManagedDashboardSnapshot(devices: tuple[DeviceSummary, ...], audit_events: tuple[AuditEvent, ...], selected: DeviceDetail | None, registry_available: bool, captured_at: str, error_code: str | None)`.
- Produces `ManagedDashboardData(query_service: DeviceQueryService, action_service: DeviceActionService, *, refresh_interval: float = 2.0, now: Callable[[], datetime] = utc_now)` with `refresh() -> ManagedDashboardSnapshot`, `refresh_if_stale() -> ManagedDashboardSnapshot`, `snapshot() -> ManagedDashboardSnapshot`, `disconnect(agent_id: str, actor: str, reason: str) -> ActionResult`, and `revoke(agent_id: str, actor: str, reason: str) -> ActionResult`.
- Extends `build_app(data: DashboardData, title: str = "PhantomLink C2 - Live Dashboard", refresh_interval: float = 2.0, managed_data: ManagedDashboardData | None = None)` with `Legacy` and `Managed Agents` tabs while preserving the first three positional parameters.

- [ ] **Step 1: Write failing cache and degraded-state tests**

```python
def test_registry_failure_keeps_labeled_last_snapshot_and_disables_actions(query, actions, fixed_now):
    data = ManagedDashboardData(query, actions, now=lambda: fixed_now)
    healthy = data.refresh()
    query.raise_registry_unavailable = True
    degraded = data.refresh()
    assert degraded.devices == healthy.devices
    assert degraded.registry_available is False
    assert degraded.error_code == "REGISTRY_UNAVAILABLE"
    assert degraded.captured_at == healthy.captured_at
    result = data.revoke(healthy.devices[0].agent_id, "operator", "retired")
    assert result.code == "FAILED"
    assert actions.revoke_calls == []
```

- [ ] **Step 2: Run the focused tests and verify the managed adapter is missing**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_managed_dashboard.py tests/test_dashboard.py -q
```

Expected: collection exits nonzero with missing `C2.managed_dashboard`.

- [ ] **Step 3: Implement the immutable two-second managed cache**

Use one lock around the snapshot reference, not around repository I/O. `refresh()` calls query services outside the lock, builds a complete immutable snapshot, then swaps it. On `RegistryUnavailable`, retain the previous devices/audit/detail and previous capture timestamp, set `registry_available=False`, and set `error_code="REGISTRY_UNAVAILABLE"`. If no healthy snapshot exists, use empty tuples but still label them unavailable.

Add `notify_event()` using a bounded `queue.Queue(maxsize=256)` only as a refresh hint. On saturation, discard the duplicate hint; the two-second poll remains authoritative. No event contains a socket or credential.

- [ ] **Step 4: Write headless Textual tests for columns, tabs, keys, and confirmation**

```python
def test_managed_tab_has_required_columns_and_text_status(app):
    async def scenario():
        async with app.run_test() as pilot:
            await pilot.press("m")
            table = app.query_one("#managed-devices", DataTable)
            assert [str(column.label) for column in table.columns.values()] == [
                "Status", "Device", "Agent ID", "VPN IP", "Last Seen", "Certificate Expiry",
            ]
            assert "ONLINE" in table.get_row_at(0)
    asyncio.run(scenario())


def test_revoke_requires_short_id_and_reason(app, action_service):
    async def scenario():
        async with app.run_test() as pilot:
            await pilot.press("m", "r")
            await pilot.press(*"wrong-id", "tab", *"retired", "enter")
            assert action_service.revoke_calls == []
            assert "agent ID does not match" in app.query_one("#managed-message", Static).renderable.plain
    asyncio.run(scenario())
```

Also cover `D` confirmation, `F` filtering by display name/agent ID/status, `Q` quit, arrow/Tab navigation, a registry-unavailable banner, disabled actions, selected detail fields, recent audit columns, and the unchanged legacy table.

- [ ] **Step 5: Compose the Managed and Legacy tabs with worker-backed actions**

Keep current `DashboardData` and legacy table rendering. Add `TabbedContent` with `Legacy` first and `Managed Agents` second. Managed columns are exactly `Status`, `Device`, `Agent ID`, `VPN IP`, `Last Seen`, and `Certificate Expiry`. Detail text includes version, fingerprint, enrollment time, last heartbeat, and revocation metadata. Audit text includes timestamp, action, result, actor, target, and reason.

Use Textual workers for refresh, disconnect, and revoke calls. UI callbacks only apply completed immutable snapshots/results. `D` shows a yes/no confirmation. `R` requires the selected device's first eight agent-ID characters and a non-empty reason. State uses both literal text and color. Bind `F` to a focusable filter input and `Q` to quit.

- [ ] **Step 6: Run managed and legacy dashboard tests repeatedly**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_managed_dashboard.py tests/test_dashboard.py tests/test_console_dashboard.py -q
1..20 | ForEach-Object { .\.venv\Scripts\python.exe -m pytest tests/test_managed_dashboard.py tests/test_dashboard.py -q; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE } }
```

Expected: all runs pass, legacy rows still render, and teardown produces no Textual `NoMatches` errors.

- [ ] **Step 7: Commit the dashboard slice**

```powershell
git add C2/managed_dashboard.py C2/dashboard.py tests/test_managed_dashboard.py tests/test_dashboard.py
git diff --cached --check
git diff --cached --name-only
git commit -m "feat: add managed agents dashboard"
```

Expected staged paths: exactly the four paths above.

---

### Task 7: Controller composition, migration gate, and operator runbook

**Files:**
- Modify: `config.py:35-48`
- Modify: `.env.example`
- Modify: `C2/C2.py:18-55,884-1012,1387-1395`
- Modify: `C2/managed_auth.py:1018-1053`
- Modify: `C2/dashboard.py:217-247`
- Modify: `client/managed_agent.py:158-285`
- Create: `docs/runbooks/managed-agent-phase2-private-network.md`
- Modify: `tests/test_managed_auth.py`
- Modify: `tests/test_dashboard.py`
- Modify: `tests/test_phantomlink_config_import.py`

**Interfaces:**
- Consumes all Phase 2 components from Tasks 1 through 6.
- Produces controller configuration `PHANTOMLINK_MANAGED_HOST`, `PHANTOMLINK_MANAGED_DB`, `PHANTOMLINK_CA_CERT`, `PHANTOMLINK_CA_KEY`, existing `PHANTOMLINK_TLS_CERT`, `PHANTOMLINK_TLS_KEY`, `PHANTOMLINK_MANAGED_PORT`, and `PHANTOMLINK_ENROLLMENT_PORT`.
- Produces CLI commands `init-ca`, `issue-token`, `list-devices`, `list-audit`, `disconnect`, and `revoke` through `python -m C2.managed_auth`.

- [ ] **Step 1: Write failing configuration and startup-order tests**

```python
def test_managed_services_require_complete_phase2_configuration(monkeypatch, tmp_path):
    monkeypatch.setenv("PHANTOMLINK_MANAGED_HOST", "10.8.0.1")
    monkeypatch.setenv("PHANTOMLINK_MANAGED_DB", str(tmp_path / "managed.db"))
    monkeypatch.delenv("PHANTOMLINK_CA_KEY", raising=False)
    config = reload_config()
    assert config.managed_phase2_enabled() is False


def test_startup_backs_up_phase1_files_before_database_creation(tmp_path, composition_probe):
    store = tmp_path / "managed-store"
    store.mkdir()
    (store / "devices.bin").write_bytes(b"legacy")
    composition_probe.start(store)
    assert composition_probe.calls[:2] == ["backup_phase1_stores", "registry.initialize"]
```

- [ ] **Step 2: Run composition tests and verify missing configuration fails**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_phantomlink_config_import.py tests/test_managed_auth.py tests/test_dashboard.py -q
```

Expected: failures identify missing database/CA configuration and the old JSON `_store_services` composition.

- [ ] **Step 3: Add complete configuration validation and one composition path**

`managed_phase2_enabled()` returns true only when all seven managed path/host values are non-empty and the four required certificate/key files exist. A partial configuration emits one stable startup error and starts neither managed listener. Before database initialization, call `backup_phase1_stores(Path(MANAGED_STORE), Path(MANAGED_STORE) / "phase1-backup")`.

Build in this order: validate exact bind; backup legacy stores; initialize registry; load CA; build session/query/action services; build managed and enrollment servers; pass managed data into the existing dashboard; start listener threads. Shutdown in reverse order: stop enrollment acceptance, stop managed acceptance, close all live sessions/workers, stop dashboard, close repository-owned resources. SQLite has no persistent connection to close.

- [ ] **Step 4: Add stable CLI behavior without credential output**

Commands and exit codes:

```text
init-ca: 0 created/already initialized, 2 bad arguments, 5 storage failure
issue-token: 0 and the token on stdout, 2 invalid TTL, 5 registry failure
list-devices/list-audit: 0 JSON display-safe values, 5 registry failure
disconnect/revoke: 0 success/idempotent, 1 not found, 2 invalid arguments, 5 failed
```

`revoke` requires `--agent-id`, `--actor`, and `--reason`; `disconnect` requires `--agent-id` and `--actor`, with optional `--reason`. No CLI prints certificate PEM, fingerprint beyond display output, token digest, key path content, or DPAPI data.

- [ ] **Step 5: Write the exact two-machine runbook**

The runbook must include:

1. Prerequisite proof that the operator configured the VPN outside PhantomLink.
2. `Get-NetIPAddress` command to select the exact controller VPN IP and an explicit prohibition on wildcard binding.
3. CA initialization, server certificate placement, environment variables, database path, controller start, and listener checks with `Get-NetTCPConnection`.
4. Token issue to a BOM-free, private-ACL file; agent config creation; pin calculation; enrollment; foreground run; and clean stop.
5. Dashboard navigation, filter, disconnect, revoke, and expected state transitions.
6. Certificate renewal observation and signing-key-unavailable behavior.
7. Restart test requiring manual process restart and automatic device reconnect without re-enrollment.
8. Packet capture acceptance: no plaintext heartbeat or identity content.
9. Negative listener proof: no LAN/public/wildcard listener.
10. Backup, recovery, database integrity, logs, known ceilings, and rollback commands.

Use literal PowerShell commands and expected outputs for all automated checks. Mark the two-machine and packet-capture results `PENDING MANUAL ACCEPTANCE` until actually executed; do not pre-fill successful evidence.

- [ ] **Step 6: Run composition, CLI, config, and legacy regressions**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_managed_auth.py tests/test_dashboard.py tests/test_phantomlink_config_import.py tests/test_commands_registry.py tests/test_agent_runtime_integration.py -q
.\.venv\Scripts\python.exe -m compileall -q C2 client config.py
```

Expected: all tests pass and compileall exits 0.

- [ ] **Step 7: Commit composition and runbook**

```powershell
git add config.py .env.example C2/C2.py C2/managed_auth.py C2/dashboard.py client/managed_agent.py docs/runbooks/managed-agent-phase2-private-network.md tests/test_managed_auth.py tests/test_dashboard.py tests/test_phantomlink_config_import.py
git diff --cached --check
git diff --cached --name-only
git commit -m "feat: compose phase two managed services"
```

Expected staged paths: exactly the ten paths above.

---

### Task 8: Real mTLS integration, security regression, reversible evidence, and cleanup proof

**Files:**
- Create: `tests/test_phase2_integration.py`
- Modify: `tests/test_agent_runtime_integration.py`
- Modify: `tests/test_managed_auth.py`
- Modify: `tests/test_managed_services.py`
- Modify: `docs/runbooks/managed-agent-phase2-private-network.md`
- Create: `scripts/rollback-managed-agent-phase2.ps1`
- Create: `debug-artifacts/managed-agent-phase2.patch`
- Create: `debug-artifacts/managed-agent-phase2-verification.md`
- Preserve as evidence: `debug-artifacts/managed-agent-phase2-preflight/`
- Delete only after proof: obsolete Phase 1 runtime store classes inside `C2/managed_auth.py`; no Phase 1 documentation or evidence file is deleted

**Interfaces:**
- Consumes the complete Phase 2 composition.
- Produces one end-to-end test harness using a real ephemeral CA, real agent certificate, real TLS sockets, a temporary SQLite database, and loopback enabled only through the explicit test flag.
- Produces a byte-identical reproducible patch, verified rollback script, and literal verification record.

- [ ] **Step 1: Write the real end-to-end lifecycle tests**

```python
def test_enroll_online_disconnect_reconnect_revoke_rejects(tmp_path, phase2_system):
    system = phase2_system(tmp_path, allow_loopback=True)
    token = system.registry.issue_token(600)
    identity = system.agent.enroll(token)
    first = system.agent.start(identity)
    assert system.wait_for_state(identity.agent_id, "ONLINE", timeout=5)
    assert system.actions.disconnect(identity.agent_id, "integration", "test").code == "DISCONNECTED"
    assert system.wait_for_state(identity.agent_id, "ONLINE", timeout=10)
    assert system.actions.revoke(identity.agent_id, "integration", "retired").code == "REVOKED"
    assert system.wait_for_state(identity.agent_id, "REVOKED", timeout=5)
    assert system.agent.wait_for_auth_rejection(first, timeout=10)


def test_controller_restart_reloads_registry_and_agent_reconnects_without_enrollment(tmp_path, phase2_system):
    system = phase2_system(tmp_path, allow_loopback=True)
    identity = system.agent.enroll(system.registry.issue_token(600))
    system.agent.start(identity)
    assert system.wait_for_state(identity.agent_id, "ONLINE", 5)
    system.restart_controller()
    assert system.wait_for_state(identity.agent_id, "ONLINE", 10)
    assert system.enrollment_request_count == 1
```

Add real tests for wrong CA, absent client certificate, unknown certificate, mismatched agent URI/fingerprint, revoked certificate, token replay, duplicate session replacement, heartbeat timeout, registry lock/busy timeout, signing-key absence with an existing session, bounded worker saturation, and clean shutdown.

- [ ] **Step 2: Run the new integration suite three times**

```powershell
1..3 | ForEach-Object { .\.venv\Scripts\python.exe -m pytest tests/test_phase2_integration.py -q; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE } }
```

Expected: each run passes with no leaked non-daemon thread and no occupied test port.

- [ ] **Step 3: Prove removal candidates before deleting production Phase 1 store code**

```powershell
git grep -n -E "EnrollmentStore|DeviceRegistry|DpapiCredentialStore|key_id|build_proof" -- ':!docs/superpowers/specs/2026-08-12-managed-background-agent-design.md' ':!docs/superpowers/plans/2026-08-12-managed-background-agent.md' ':!docs/runbooks/managed-agent-phase1.md' ':!debug-artifacts/*'
```

Delete a Phase 1 managed store class or import only when the output proves it has no production caller and the Phase 2 replacement is named in the Task 1-4 tests. Keep `client.transport.build_proof` if the legacy protocol still imports it. Keep every Phase 1 Markdown/patch/verification artifact. Record each code deletion and retained replacement in the Phase 2 verification file.

- [ ] **Step 4: Run the complete automated verification gate**

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m compileall -q C2 client config.py
.\.venv\Scripts\python.exe -m pip check
git diff --check
```

Expected: full suite passes with no new warnings; compileall, pip check, and diff check exit 0. Record the literal commands, outputs, warnings, and exit statuses rather than summarizing them.

- [ ] **Step 5: Run dependency and Windows security scans honestly**

```powershell
.\.venv\Scripts\python.exe -m pip audit -r requirements.txt
$managed = @('C2\managed_registry.py','C2\managed_pki.py','C2\managed_services.py','C2\managed_auth.py','client\managed_identity.py','client\agent_runtime.py','client\managed_agent.py')
foreach ($path in $managed) { Start-MpScan -ScanType CustomScan -ScanPath (Resolve-Path $path) }
Get-MpThreatDetection | Select-Object InitialDetectionTime,ThreatName,Resources
```

Record exact tool availability and results. A dependency advisory or Defender finding stays a named open concern; do not rewrite tests, hide files, or weaken scanning to obtain a green record.

- [ ] **Step 6: Commit the verified lifecycle tests and dead-code cleanup**

```powershell
git add C2/managed_auth.py tests/test_phase2_integration.py tests/test_agent_runtime_integration.py tests/test_managed_auth.py tests/test_managed_services.py docs/runbooks/managed-agent-phase2-private-network.md
git diff --cached --check
git diff --cached --name-only
git commit -m "test: add phase two lifecycle verification"
```

Expected staged paths: exactly the six paths above. The implementation HEAD now contains every production and test file that the reversible patch must reproduce.

- [ ] **Step 7: Generate the exact Phase 2 patch and rollback script**

Generate the patch from the approved base while excluding the patch, verification record, rollback script, and preserved untracked paths from self-inclusion:

```powershell
.\.venv\Scripts\python.exe -c "import pathlib,subprocess; args=['git','diff','--binary','af64499','HEAD','--','.',':(exclude)debug-artifacts/managed-agent-phase2.patch',':(exclude)debug-artifacts/managed-agent-phase2-verification.md',':(exclude)scripts/rollback-managed-agent-phase2.ps1',':(exclude)debug-artifacts/managed-agent-phase2-preflight/**',':(exclude)debug-artifacts/managed-agent-preflight/**',':(exclude)debug-artifacts/task4-cli-store/**',':(exclude)debug-artifacts/task4-current.diff']; pathlib.Path('debug-artifacts/managed-agent-phase2.patch').write_bytes(subprocess.check_output(args))"
Get-FileHash debug-artifacts/managed-agent-phase2.patch -Algorithm SHA256
git apply --reverse --check debug-artifacts/managed-agent-phase2.patch
```

`scripts/rollback-managed-agent-phase2.ps1` accepts `-RepoRoot` defaulting to the current repository and `-PatchPath` defaulting to the generated patch below that root. It resolves both paths, rejects a patch outside `RepoRoot`, verifies `git -C $RepoRoot apply --reverse --check`, saves current `git status --short` and patch hash, applies reverse, then runs the rollback verification commands. It does not delete the SQLite database, CA, certificates, or backups; it prints their paths for operator-controlled archival.

- [ ] **Step 8: Verify rollback in a disposable worktree**

```powershell
$python = (Resolve-Path .\.venv\Scripts\python.exe).Path
$sourceRollback = (Resolve-Path .\scripts\rollback-managed-agent-phase2.ps1).Path
$rollbackRoot = Join-Path $env:TEMP 'phantomlink-phase2-rollback'
if (Test-Path $rollbackRoot) { git worktree remove --force $rollbackRoot }
git worktree add --detach $rollbackRoot HEAD
New-Item -ItemType Directory -Force "$rollbackRoot\debug-artifacts" | Out-Null
Copy-Item .\debug-artifacts\managed-agent-phase2.patch "$rollbackRoot\debug-artifacts\managed-agent-phase2.patch"
& $sourceRollback -RepoRoot $rollbackRoot -PatchPath "$rollbackRoot\debug-artifacts\managed-agent-phase2.patch"
Push-Location $rollbackRoot
& $python -m pytest tests/test_client_transport.py tests/test_protocol_auth.py tests/test_encryption.py tests/test_dashboard.py -q
& $python -c "import client.PhantomLink; import C2.dashboard; print('ROLLBACK_IMPORT_OK')"
Pop-Location
git worktree remove --force $rollbackRoot
```

Expected: reverse check and rollback exit 0; legacy tests pass; literal output includes `ROLLBACK_IMPORT_OK`.

- [ ] **Step 9: Complete the verification record and manual-acceptance boundary**

`debug-artifacts/managed-agent-phase2-verification.md` records:

- base commit `af64499`, final commit, patch SHA-256, and byte count;
- exact automated baseline and modified commands, inputs, literal outputs, warnings, and exit statuses;
- exact loopback integration runs and concurrency repetitions;
- exact scan commands/results and any open concern;
- exact patch generation, reverse check, rollback, import, and forward-apply proof;
- the runbook's two-machine, packet-capture, reboot, and non-wildcard checks as `PENDING MANUAL ACCEPTANCE` until observed on two Windows machines.

Do not describe the system as deployable before those manual items have literal evidence.

- [ ] **Step 10: Reproduce the patch byte-for-byte and commit evidence**

```powershell
.\.venv\Scripts\python.exe -c "import os,pathlib,subprocess; args=['git','diff','--binary','af64499','HEAD','--','.',':(exclude)debug-artifacts/managed-agent-phase2.patch',':(exclude)debug-artifacts/managed-agent-phase2-verification.md',':(exclude)scripts/rollback-managed-agent-phase2.ps1',':(exclude)debug-artifacts/managed-agent-phase2-preflight/**',':(exclude)debug-artifacts/managed-agent-preflight/**',':(exclude)debug-artifacts/task4-cli-store/**',':(exclude)debug-artifacts/task4-current.diff']; pathlib.Path(os.environ['TEMP'],'managed-agent-phase2-reproduced.patch').write_bytes(subprocess.check_output(args))"
$committed = Get-FileHash debug-artifacts/managed-agent-phase2.patch -Algorithm SHA256
$reproduced = Get-FileHash "$env:TEMP\managed-agent-phase2-reproduced.patch" -Algorithm SHA256
if ($committed.Hash -ne $reproduced.Hash) { throw 'documented patch command is not reproducible' }
git apply --reverse --check debug-artifacts/managed-agent-phase2.patch
```

Commit only evidence roles and the retained preflight record:

```powershell
git add scripts/rollback-managed-agent-phase2.ps1 debug-artifacts/managed-agent-phase2.patch debug-artifacts/managed-agent-phase2-verification.md debug-artifacts/managed-agent-phase2-preflight
git diff --cached --check
git diff --cached --name-only
git commit -m "docs: record phase two verification and rollback"
git status --short
```

Expected: commit succeeds; final status contains only the three preserved pre-existing untracked artifact paths. Request a final correctness/security review against every completion gate in Design sections 13 through 17.

---

## Plan self-review record

- **Spec coverage:** Tasks 1-8 cover scope, architecture, certificate/network model, four-table schema, immutable contracts, state/action semantics, dual-tab dashboard, audit requirements, failure behavior, Phase 1 backup/re-enrollment, security limits, automated integration, two-machine acceptance, completion gates, and Empire provenance.
- **Scope split check:** Registry, PKI, transport/session, actions, UI, composition, and verification are coupled by one enrollment-to-dashboard lifecycle but each task produces an independently reviewable tested deliverable. A separate sub-project would create duplicated contracts and no independently usable Phase 2 product.
- **Dependency check:** No new package is introduced. SQLite, locking, IP validation, queues, temporary files, and TLS use stdlib; certificate operations reuse installed cryptography; Windows protection reuses existing pywin32 helpers.
- **Type check:** `IssuedDeviceCertificate` lives in `managed_registry.py`; PKI returns it; enrollment persists it. `SessionManager` supplies immutable snapshots; query/action services consume the same manager; dashboard consumes only service contracts and immutable display models.
- **Deletion check:** Phase 1 documentation/evidence remains. Only dead production store code may be deleted after the exact Task 8 grep proof and replacement tests.
- **Placeholder scan:** The plan contains no deferred implementation marker. Manual hardware/network observations are explicitly bounded as pending acceptance rather than fabricated results.
