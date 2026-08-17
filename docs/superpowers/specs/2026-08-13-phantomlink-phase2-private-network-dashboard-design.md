# PhantomLink Phase 2: Private-Network mTLS and Managed Dashboard

**Status:** Approved design; pending written-spec review

**Date:** 2026-08-13

## 1. Objective

Phase 2 turns the Phase 1 managed heartbeat runtime into a two-machine,
private-network management path with a local Textual dashboard. It keeps
PhantomLink as its own product while adopting three architectural ideas from
Empire: a durable device registry, an audit log, and stable service contracts.

Phase 2 supports only:

- device enrollment;
- managed status and heartbeat;
- disconnecting a live managed session;
- revoking a managed device;
- viewing managed audit events.

Phase 2 does not add remote commands or reuse Empire's stagers, agents,
listeners, tasking, modules, bypasses, obfuscation, or UI code.

## 2. Scope Boundaries

### In scope

- Connectivity across an already-installed private VPN or overlay network.
- Exact-interface binding to a configured VPN IP.
- Pinned bootstrap TLS for enrollment.
- Per-device client certificates and mutual TLS for managed sessions.
- SQLite-backed device, enrollment-token, schema, and audit records.
- A controller-owned live-session manager.
- Internal typed query/action contracts.
- A Managed Agents view in the existing Textual TUI.
- Disconnect, revoke, certificate renewal, and bounded reconnect behavior.
- Automated loopback tests and a required manual two-Windows-machine test.

### Out of scope

- VPN installation, configuration, account provisioning, or routing.
- Public-internet listeners or wildcard binding.
- Remote commands, arbitrary tasking, file transfer, shell access, or modules.
- Stagers, persistence, Windows Service installation, installer, or updater.
- Web dashboard, externally exposed REST API, or multi-operator access.
- AV/EDR bypasses, obfuscation, injection, or detection-evasion work.
- Automatic conversion of Phase 1 shared-secret development credentials.

## 3. Architecture

```text
Managed Agent (Windows)
  |-- external VPN interface
  |-- pinned bootstrap enrollment client
  |-- locally generated private key and CSR
  |-- DPAPI-protected certificate bundle
  `-- mTLS heartbeat runtime
                 |
                 | private VPN only
                 v
Controller
  |-- Enrollment Service
  |-- Managed mTLS Listener
  |-- Session Manager
  |-- SQLite Registry Repository
  |-- Audit Service
  `-- Managed Device Service
                 |
                 v
Local Textual TUI
  |-- Managed Agents view
  |-- Device detail
  |-- Recent audit events
  |-- Disconnect
  `-- Revoke
```

The VPN supplies reachability, not identity. Mutual TLS supplies device
authentication. The dashboard never reads SQLite or manipulates sockets
directly; it calls the Managed Device Service.

The Phase 1 legacy client, listener, and dashboard data flow remain separate.
The TUI exposes Managed and Legacy views as separate tabs so managed actions
cannot accidentally target a legacy client.

## 4. Components and Responsibilities

### 4.1 Agent certificate store

The agent generates its own asymmetric key pair and CSR. The private key never
leaves that machine. The issued client certificate, chain, and private key are
stored as one versioned credential bundle protected with Current User DPAPI and
the existing private-file ACL checks.

The stored non-secret identity contains `agent_id`, certificate fingerprint,
certificate expiry, and credential format version. It contains no token or
private-key material.

### 4.2 Enrollment Service

The enrollment endpoint uses server-authenticated TLS and the existing
certificate pin. It accepts exactly a one-time token and a validated CSR. In
one transaction it consumes the token, creates the device, records the issued
certificate identity, and appends the enrollment audit event. It returns only
the signed certificate chain and non-secret device identity.

The service rejects expired, consumed, malformed, or unknown tokens; malformed
CSRs; unsupported algorithms; invalid identity fields; and requests exceeding
the bounded body size.

### 4.3 Managed mTLS Listener

The listener requires a client certificate signed by the controller CA. After
the TLS handshake it extracts the certificate `agent_id`, serial, and SHA-256
fingerprint and verifies them against the registry. Unknown, expired, mismatched,
or revoked devices are closed before session registration and produce a
redacted audit event.

The listener retains the Phase 1 bounded worker admission, handshake deadlines,
single socket owner, heartbeat deadlines, and bounded shutdown behavior. It
does not fall back to the legacy transport.

### 4.4 Session Manager

The Session Manager owns the in-memory mapping of `agent_id` to the current
authenticated session. Only one managed session may be active per device. A
new valid reconnect atomically replaces and closes the old session and records
`SESSION_REPLACED`.

The Session Manager supplies live state to the service layer. `ONLINE` is never
inferred from a stale database value.

### 4.5 Registry Repository

The repository is the only code allowed to execute managed-registry SQL. It
uses Python's `sqlite3`, WAL mode, foreign keys, explicit transactions, one
connection per operation, and a bounded busy timeout. Schema changes use small,
ordered migrations recorded in `schema_version`.

### 4.6 Managed Device Service

The service joins durable registry information with live Session Manager state
and exposes typed query/action contracts to the TUI. It enforces authorization
rules, idempotency, audit requirements, and action ordering. This is an internal
Python boundary, not an HTTP API in Phase 2.

### 4.7 Textual TUI

The existing Textual application gains a Managed Agents tab. Database queries
and actions run in Textual workers and return immutable snapshots to the render
loop. The existing Legacy view keeps its current behavior and data source.

## 5. Network and Certificate Model

### 5.1 Binding rules

- Production requires one explicit IP assigned to the VPN interface.
- `0.0.0.0`, `::`, multicast, broadcast, and wildcard hostnames are rejected.
- Loopback is allowed only when an explicit test-mode configuration is active.
- The controller does not open or alter firewall rules.
- The runbook must verify that no managed listener is reachable through a LAN
  or public interface.

### 5.2 Enrollment sequence

1. The agent generates a key pair and CSR.
2. The agent connects to the enrollment endpoint over pinned TLS.
3. The agent sends the CSR and a 600-second one-time token.
4. The controller validates the CSR and atomically consumes the token.
5. The controller assigns a UUID `agent_id` and signs the client certificate.
6. The agent validates the returned chain before storing the DPAPI bundle.
7. The agent opens the managed mTLS connection.

An enrollment failure must not leave a usable partial device or reusable token.

### 5.3 Managed session

- TLS minimum version: TLS 1.2; TLS 1.3 is used when available.
- Client certificate validity: 90 days.
- Renewal begins when no more than 30 days remain.
- Heartbeat interval: 30 seconds.
- Agent read deadline: 90 seconds.
- Retry: the existing bounded exponential backoff with jitter.
- The certificate identity and registry record must agree before the session is
  considered authenticated.

### 5.4 Certificate renewal

Renewal runs through the existing authenticated mTLS session. The agent creates
a new key and CSR. The controller signs it only if the current device is active,
the current certificate is valid, and the registry identity matches. The new
fingerprint replaces the old one in one transaction with a renewal audit event.

Revoked devices cannot renew. A device whose certificate has already expired
must enroll again with a new one-time token.

### 5.5 Controller keys

The controller CA private key is stored outside SQLite in a DPAPI-protected,
private-ACL file. The database stores public certificate metadata only. If the
signing key is unavailable, enrollment and renewal fail closed while existing
authenticated heartbeat sessions may continue.

## 6. Registry Schema

### `schema_version`

- `version INTEGER PRIMARY KEY`
- `applied_at TEXT NOT NULL`

### `devices`

- `agent_id TEXT PRIMARY KEY`
- `display_name TEXT NOT NULL`
- `certificate_fingerprint TEXT UNIQUE NOT NULL`
- `certificate_serial TEXT UNIQUE NOT NULL`
- `certificate_not_after TEXT NOT NULL`
- `agent_version TEXT NOT NULL`
- `last_vpn_ip TEXT`
- `enrolled_at TEXT NOT NULL`
- `last_seen_at TEXT`
- `revoked_at TEXT`
- `revocation_reason TEXT`

### `enrollment_tokens`

- `token_digest TEXT PRIMARY KEY`
- `created_at TEXT NOT NULL`
- `expires_at TEXT NOT NULL`
- `consumed_at TEXT`

Only a cryptographic token digest is stored.

### `audit_events`

- `id INTEGER PRIMARY KEY AUTOINCREMENT`
- `occurred_at TEXT NOT NULL`
- `actor TEXT NOT NULL`
- `action TEXT NOT NULL`
- `target_agent_id TEXT`
- `result TEXT NOT NULL`
- `reason TEXT`
- `correlation_id TEXT NOT NULL`
- `details_json TEXT NOT NULL`

`details_json` is an allowlisted, size-bounded JSON object. Tokens, private keys,
certificate bundles, DPAPI blobs, and credential material are forbidden.

There is no sessions table in Phase 2. Live sessions are runtime state;
connection history is represented by audit events and `last_seen_at`.

## 7. States and Contracts

### 7.1 Device states

```text
ENROLLED -> ONLINE -> OFFLINE
    `----------------> REVOKED
```

- `ENROLLED`: certificate issued but no live session exists yet.
- `ONLINE`: an authenticated live session exists.
- `OFFLINE`: the device connected previously but has no live session.
- `REVOKED`: durable registry state; connection and renewal are forbidden.

### 7.2 Typed models

- `DeviceSummary`
- `DeviceDetail`
- `AuditEvent`
- `ActionResult`

Models contain only display-safe, immutable values.

### 7.3 Query contract

```python
class DeviceQueryService:
    def list_devices(self) -> tuple[DeviceSummary, ...]: ...
    def get_device(self, agent_id: str) -> DeviceDetail | None: ...
    def list_audit_events(self, limit: int = 100) -> tuple[AuditEvent, ...]: ...
```

### 7.4 Action contract

```python
class DeviceActionService:
    def disconnect(self, agent_id: str, actor: str, reason: str) -> ActionResult: ...
    def revoke(self, agent_id: str, actor: str, reason: str) -> ActionResult: ...
```

`ActionResult` returns a stable code such as `DISCONNECTED`, `ALREADY_OFFLINE`,
`REVOKED`, `ALREADY_REVOKED`, `NOT_FOUND`, or `FAILED` plus a display-safe
message and correlation ID.

## 8. Action Semantics

### Disconnect

1. Validate device, actor, and bounded reason.
2. Append and commit `DISCONNECT_REQUESTED` before touching the socket.
3. Close the current session if one exists.
4. Append the result event.
5. Return `DISCONNECTED` or `ALREADY_OFFLINE`.

The device remains active and may reconnect. If the initial audit write fails,
the disconnect is not attempted.

### Revoke

1. Validate device, actor, and required bounded reason.
2. In one transaction set `revoked_at` and append the revoke audit event.
3. After commit, close every current session for the device.
4. Reject all later handshakes and renewals.

Revocation is idempotent. Repeated calls return `ALREADY_REVOKED`. There is no
unrevoke action in Phase 2; the machine must enroll as a new device.

If revoke races with connect, the durable revoked state wins. Session
registration rechecks registry state immediately before publishing the session.

## 9. Dashboard UX

The Managed Agents tab contains:

```text
Status | Device | Agent ID | VPN IP | Last Seen | Certificate Expiry
```

The selected device shows version, fingerprint, enrollment time, last
heartbeat, and revocation metadata. A recent-audit pane shows timestamp, action,
result, actor, target, and reason.

Keyboard actions:

- `D`: disconnect after a confirmation dialog.
- `R`: revoke after typing the short agent ID and a required reason.
- `F`: filter devices.
- `Q`: quit.

Status is communicated by both text and color. Keyboard navigation covers every
action. The default refresh interval is two seconds, with immediate in-process
events for heartbeat and disconnect changes.

If the registry is unavailable, the TUI shows `REGISTRY UNAVAILABLE`, retains
the last successful snapshot and timestamp, disables state-changing actions,
and leaves controller sessions running. It never substitutes fabricated state.

## 10. Audit Requirements

Audit events are required for:

- enrollment and certificate renewal;
- authentication success and rejection;
- connected, disconnected, replaced, and heartbeat timeout;
- operator disconnect and result;
- revoke;
- registry, certificate, and action failures.

Audit events are append-only through the repository interface. Phase 2 adds no
delete or edit operation. Retention/export policy is deferred; SQLite growth is
an explicit operational ceiling to revisit after measured usage.

## 11. Error Handling

- VPN or controller unavailable: agent retries with bounded backoff; UI becomes
  offline after the heartbeat deadline.
- Unknown, expired, mismatched, or revoked certificate: reject, close, and audit.
- Registry unavailable during authentication or a state-changing action: fail
  closed.
- Registry unavailable during display: use the last labeled snapshot and
  disable actions.
- Signing key unavailable: disable enrollment and renewal; do not downgrade
  authentication.
- Queue saturation or worker exhaustion: reject excess work promptly and retain
  bounded shutdown.
- Controller restart: reload durable registry; active devices reconnect without
  re-enrollment.
- Duplicate valid connection: atomically replace the older session and audit it.

Logs and exceptions use stable event codes and allowlisted metadata. They never
include tokens, private keys, certificate bundles, or DPAPI blobs.

## 12. Migration and Compatibility

Phase 1 has no production deployment to migrate. On first Phase 2 startup:

1. Detect any Phase 1 managed token/device stores.
2. Hash and back them up before creating the SQLite registry.
3. Do not translate shared-secret development credentials into certificates.
4. Require those development devices to enroll again.

The agent config loader retains existing Phase 1 timing, pin, port, and logging
fields. Controller configuration adds explicit VPN bind, database, CA
certificate, and CA key paths. Legacy client/listener/dashboard behavior and
protocol remain regression-tested and separate from the managed service.

## 13. Security Properties and Limits

Phase 2 removes the need for a global agent secret. Compromise of one agent's
DPAPI-protected key does not disclose another device's key or the controller CA
key. Certificate pinning protects bootstrap enrollment, mTLS proves possession
of each device key, exact-interface binding limits network exposure, and durable
revocation prevents a revoked identity from reconnecting.

These controls do not make the Python program resistant to source inspection or
guarantee that endpoint security products will not detect it. An administrator
or attacker controlling an enrolled Windows account may inspect process memory,
invoke the program, or use that account's DPAPI context. Controller compromise
or CA-key compromise affects every device and requires CA rotation and fresh
enrollment, which is deferred from Phase 2. Signed packaging, service hardening,
anti-tamper controls, and installer trust belong to a later deployment phase.

## 14. Verification Strategy

### Unit tests

- schema creation and ordered migration;
- token issue, expiry, atomic consumption, and replay rejection;
- CSR and certificate validation;
- certificate fingerprint and `agent_id` matching;
- service contracts, states, and idempotent actions;
- audit allowlisting and secret redaction;
- configuration and exact-interface validation.

### Concurrency and security tests

- simultaneous enrollment, renewal, reconnect, and revoke;
- revoke-versus-session-registration race;
- cross-process registry updates without lost writes;
- bounded handshake/session workers and clean shutdown;
- wrong CA, wrong identity, expired certificate, and revoked certificate;
- wildcard/public binding rejection;
- unavailable/locked database behavior.

### Integration tests

- real ephemeral CA, server certificate, client CSR, and client certificate;
- pinned bootstrap enrollment over loopback;
- mTLS heartbeat, reconnect, renewal, disconnect, and revoke;
- controller restart and identity persistence;
- heartbeat timeout and zero leaked threads/sockets;
- rollback in a disposable worktree.

### TUI tests

- headless Managed and Legacy tabs;
- immutable snapshot rendering;
- keyboard confirmation and action results;
- live event refresh;
- registry degraded mode;
- teardown with no late-refresh exception.

## 15. Two-Machine Acceptance

Phase 2 is not deployable until the following passes on two Windows machines
using an existing private VPN:

1. The controller listens only on its configured VPN IP.
2. The agent enrolls with one one-time token.
3. The dashboard shows `ONLINE` and fresh heartbeat data.
4. Removing VPN connectivity produces `OFFLINE` within 90 seconds.
5. Restoring VPN connectivity produces an automatic authenticated reconnect.
6. Disconnect closes the live session and permits reconnect.
7. Revoke closes the session and rejects every reconnect and renewal.
8. After restarting both machines, manually starting the controller and agent
   processes preserves identity and registry state; automatic startup is not
   implied because service installation is out of scope.
9. A packet capture contains no plaintext heartbeat or identity payload.
10. No managed listener is reachable through LAN or public interfaces.

The acceptance record includes exact commands, literal outputs, relevant
configuration hashes, log hashes, certificate fingerprints, and exit statuses.
It excludes tokens and private-key material.

## 16. Completion Gates

- All focused and full tests pass with no new warnings.
- No unhandled worker/thread exceptions occur.
- Schema migration, patch, and runnable rollback are verified in clean worktrees.
- Dependency, static, and Defender scans are recorded honestly; a clean scan is
  evidence for that build and definition set, not a future-detection guarantee.
- No token, private key, certificate bundle, or credential appears in source,
  config, audit, logs, patch evidence, or Git history.
- The manual two-machine acceptance passes before Phase 2 is called deployable.
- Final review finds no open Critical or Important issue.

## 17. Empire Inspiration and Code Provenance

PhantomLink borrows only the architectural concepts of a durable registry,
event/audit boundaries, and stable service contracts. Phase 2 does not depend on
Empire at runtime.

Direct code reuse is exceptional rather than the default. Any Empire-derived
code must be isolated, reviewed for compatibility, recorded in a provenance
file, and retain the BSD-3-Clause copyright, conditions, and disclaimer.
Dependencies, bundled modules, submodules, and Starkiller require their own
license review; the repository-level Empire license is not treated as a blanket
license for unrelated third-party content.

References:

- <https://github.com/BC-SECURITY/Empire>
- <https://bc-security.gitbook.io/empire-wiki/restful-api>
- <https://github.com/BC-SECURITY/Empire/blob/main/LICENSE>

## 18. Deferred Work

- VPN automation.
- Installer, Windows Service, signed packaging, and updater.
- External REST API, web UI, and multi-operator authorization.
- Audit retention/export after actual growth is measured.
- Certificate recovery workflows beyond fresh enrollment.
- Any remote maintenance action or command channel.
