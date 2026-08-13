# Managed Agent Phase 2 Verification

## Artifact identity

- Approved base: `af64499`
- Implementation commit: `5aca7a2f9a1b190a3a86f9f7c0ab8bff33ae21c8`
- Modified artifact: `tests/test_phase2_integration.py`
- Exact patch: `debug-artifacts/managed-agent-phase2.patch`
- Rollback: `scripts/rollback-managed-agent-phase2.ps1`
- Verification record: `debug-artifacts/managed-agent-phase2-verification.md`
- Patch SHA-256: `D51F97B380F653E0E759B16B92D9511C97229734D16CA3AC2C8573E06C6B06D7`
- Patch bytes: `535897`

The patch contains `af64499..5aca7a2` and excludes this record, the patch itself, the rollback script, and all four preserved preflight paths.

## TDD record

### RED: obsolete Phase 1 runtime fixture

```text
COMMAND=.\.venv\Scripts\python.exe -m pytest tests/test_agent_runtime_integration.py -q
FFFF.FFFFFFF.FF                                                          [100%]
13 failed, 2 passed, 13 warnings in 26.81s
EXIT=1
```

The repeated thread exception was literal `TypeError: credential must be an AgentCertificateIdentity`. This proved the secret/proof fixture no longer exercised the Phase 2 runtime.

### RED: new Phase 2 integration and permanent disconnect regression

```text
COMMAND=.\.venv\Scripts\python.exe -m pytest tests/test_phase2_integration.py tests/test_managed_services.py::test_disconnect_result_audit_failure_reports_failure_after_closing_socket -q
FFFFFFFFFFFF.                                                            [100%]
12 failed, 1 passed in 2.54s
RED_EXIT=1
```

The permanent disconnect-result-audit regression passed immediately. The integration failures consistently exposed a test-state collision: SQLite directory ACL hardening made colocated CA material unreadable. Separating registry and PKI directories was the minimal fixture correction.

### GREEN: real mTLS fixture

```text
COMMAND=.\.venv\Scripts\python.exe -m pytest tests/test_phase2_integration.py -q
............                                                             [100%]
12 passed in 9.29s
EXIT=0
```

The harness uses an ephemeral CA, server and device certificates, pinned enrollment HTTPS, an on-disk SQLite registry, actual TLS sockets, and `allow_loopback=True` only inside tests. It covers enroll/online/D disconnect/reconnect/R revoke/reject, restart without reenrollment, wrong CA, missing client certificate, unknown/mismatched/revoked certificates, token replay, replacement, heartbeat timeout, database busy timeout, absent signer with a live session, worker saturation, and clean shutdown.

## Three consecutive integration runs

```text
RUN=1
COMMAND=.\.venv\Scripts\python.exe -m pytest tests/test_phase2_integration.py -q
............                                                             [100%]
12 passed in 8.86s
EXIT=0
RUN=2
COMMAND=.\.venv\Scripts\python.exe -m pytest tests/test_phase2_integration.py -q
............                                                             [100%]
12 passed in 9.15s
EXIT=0
RUN=3
COMMAND=.\.venv\Scripts\python.exe -m pytest tests/test_phase2_integration.py -q
............                                                             [100%]
12 passed in 9.59s
EXIT=0
```

Each run's teardown asserts all agent, managed-listener, managed-session, and enrollment-listener threads stop, then verifies both ephemeral ports reject new connections.

## Dead-code proof and retained compatibility

Before cleanup, the required grep showed `EnrollmentStore` and `DeviceRegistry` only inside `C2/managed_auth.py` and tests; the Phase 2 replacements were already covered by `tests/test_managed_registry.py`, `tests/test_managed_auth.py`, `tests/test_managed_services.py`, and `tests/test_phase2_integration.py`.

Deleted production readers:

- `C2.managed_auth.EnrollmentStore` -> `C2.managed_registry.ManagedRegistry.enrollment_tokens`, `issue_token`, and atomic `consume_token_and_enroll`.
- `C2.managed_auth.DeviceRegistry` -> durable `C2.managed_registry.ManagedRegistry.devices` certificate identity.
- Legacy `EnrollmentService` branches, proof-frame helpers, and secret response shape -> Phase 2 CSR enrollment HTTPS and certificate-only mTLS.

Retained:

- `client.agent_config.DpapiCredentialStore` and `key_id` compatibility code, because the exact grep still finds its Phase 1 tests and compatibility path.
- `client.transport.build_proof`, because Phase 1 transport regression coverage still imports it.
- Every Phase 1 document and evidence artifact.

After cleanup, the exact grep has no `EnrollmentStore`, `DeviceRegistry`, or proof import in `C2/managed_auth.py`; remaining hits are the retained client compatibility code, transport helper, tests, and Phase 2 plan history.

## Full automated gate

```text
COMMAND=.\.venv\Scripts\python.exe -m pytest -q
........................................................................ [ 14%]
........................................................................ [ 28%]
........................................................................ [ 42%]
........................................................................ [ 56%]
........................................................................ [ 71%]
........................................................................ [ 85%]
........................................................................ [ 99%]
...                                                                      [100%]
============================== warnings summary ===============================
.venv\Lib\site-packages\discord\player.py:30
  DeprecationWarning: 'audioop' is deprecated and slated for removal in Python 3.13

tests/test_commands_registry.py::TestCmdContextSend::test_send_prepends_cmd_prefix
  RuntimeWarning: coroutine 'AsyncMockMixin._execute_mock_call' was never awaited

507 passed, 2 warnings in 171.96s (0:02:51)
EXIT=0

COMMAND=.\.venv\Scripts\python.exe -m compileall -q C2 client config.py
EXIT=0

COMMAND=.\.venv\Scripts\python.exe -m pip check
G:\for_hack_all\Link_all - Copy\.worktrees\managed-background-agent\.venv\Scripts\python.exe: No module named pip
EXIT=1

COMMAND=uv pip check --python .\.venv\Scripts\python.exe
Checked 51 packages in 22ms
All installed packages are compatible
EXIT=0

COMMAND=git diff --check
EXIT=0
```

The two pytest warnings predate this task and remain named concerns. The exact `python -m pip check` command did not run because this uv-managed environment has no pip module; the equivalent uv compatibility check passed.

## Dependency audit

```text
COMMAND=.\.venv\Scripts\python.exe -m pip audit -r requirements.txt
G:\for_hack_all\Link_all - Copy\.worktrees\managed-background-agent\.venv\Scripts\python.exe: No module named pip
EXIT=1

COMMAND=uvx pip-audit -r requirements.txt
Found 35 known vulnerabilities in 2 packages
cryptography 41.0.7: 11 reported rows; listed fixes range from 42.0.0 through 49.0.0
pillow 10.4.0: 24 reported rows; listed fixes range from 12.1.1 through 12.3.0
EXIT=1
```

Open concern: `requirements.txt` resolves `cryptography==41.0.7` and `pillow==10.4.0`, and the current audit database reports 35 rows. Dependency upgrades were not hidden or folded into this verification change.

## Static check

```text
COMMAND=.\.venv\Scripts\ruff.exe check C2/managed_auth.py tests/test_phase2_integration.py tests/test_agent_runtime_integration.py tests/test_managed_auth.py tests/test_managed_services.py
Found 10 errors.
RUFF_EXIT=1
```

The findings are six intentional broad exception boundaries in operator/storage handling, one pre-existing nested context, one pre-existing import formatting item, one broad exception in a concurrency test, and the integration harness's `BaseException` capture that surfaces worker errors during teardown. None was suppressed or auto-fixed to obtain a green record.

## Windows Defender

Tool availability:

```text
Name                  CommandType Source
----                  ----------- ------
Start-MpScan             Function ConfigDefender
Get-MpThreatDetection    Function ConfigDefender
```

Exact custom scans:

```text
COMMAND=Start-MpScan -ScanType CustomScan -ScanPath <WORKTREE>\C2\managed_registry.py
EXIT=0
COMMAND=Start-MpScan -ScanType CustomScan -ScanPath <WORKTREE>\C2\managed_pki.py
EXIT=0
COMMAND=Start-MpScan -ScanType CustomScan -ScanPath <WORKTREE>\C2\managed_services.py
EXIT=0
COMMAND=Start-MpScan -ScanType CustomScan -ScanPath <WORKTREE>\C2\managed_auth.py
EXIT=0
COMMAND=Start-MpScan -ScanType CustomScan -ScanPath <WORKTREE>\client\managed_identity.py
EXIT=0
COMMAND=Start-MpScan -ScanType CustomScan -ScanPath <WORKTREE>\client\agent_runtime.py
EXIT=0
COMMAND=Start-MpScan -ScanType CustomScan -ScanPath <WORKTREE>\client\managed_agent.py
EXIT=0
```

Defender status and detection result:

```text
AntivirusEnabled              : True
RealTimeProtectionEnabled     : True
AntivirusSignatureVersion     : 1.457.130.0
AntivirusSignatureLastUpdated : 8/12/2026 9:46:34 PM

COMMAND=Get-MpThreatDetection | Select-Object InitialDetectionTime,ThreatName,Resources
TOTAL_HISTORICAL_DETECTIONS=186
WORKTREE_RESOURCE_DETECTIONS=0
EXIT=0
```

The unfiltered command returned unrelated historical resources elsewhere on the machine. The preserved transient transcript had SHA-256 `92DD7B59E59B7693BF174895885C2C55BE2D4987852F6FB0A9732BF180FF1A9F` and 118576 bytes. No returned resource path was below this worktree. A successful current scan is only evidence for signature `1.457.130.0` and these seven files.

## Patch generation and reproducibility

```text
COMMAND=.\.venv\Scripts\python.exe -c "import pathlib,subprocess; args=['git','diff','--binary','af64499','HEAD','--','.',':(exclude)debug-artifacts/managed-agent-phase2.patch',':(exclude)debug-artifacts/managed-agent-phase2-verification.md',':(exclude)scripts/rollback-managed-agent-phase2.ps1',':(exclude)debug-artifacts/managed-agent-phase2-preflight/**',':(exclude)debug-artifacts/managed-agent-preflight/**',':(exclude)debug-artifacts/task4-cli-store/**',':(exclude)debug-artifacts/task4-current.diff']; pathlib.Path('debug-artifacts/managed-agent-phase2.patch').write_bytes(subprocess.check_output(args))"
PATCH_GENERATE_EXIT=0
PATCH_SHA256=D51F97B380F653E0E759B16B92D9511C97229734D16CA3AC2C8573E06C6B06D7
PATCH_BYTES=535897

COMMAND=git apply --reverse --check debug-artifacts/managed-agent-phase2.patch
REVERSE_CHECK_EXIT=0

COMMAND=.\.venv\Scripts\python.exe -c "import os,pathlib,subprocess; args=['git','diff','--binary','af64499','HEAD','--','.',':(exclude)debug-artifacts/managed-agent-phase2.patch',':(exclude)debug-artifacts/managed-agent-phase2-verification.md',':(exclude)scripts/rollback-managed-agent-phase2.ps1',':(exclude)debug-artifacts/managed-agent-phase2-preflight/**',':(exclude)debug-artifacts/managed-agent-preflight/**',':(exclude)debug-artifacts/task4-cli-store/**',':(exclude)debug-artifacts/task4-current.diff']; pathlib.Path(os.environ['TEMP'],'managed-agent-phase2-reproduced.patch').write_bytes(subprocess.check_output(args))"
REPRODUCE_EXIT=0
REPRODUCED_SHA256=D51F97B380F653E0E759B16B92D9511C97229734D16CA3AC2C8573E06C6B06D7
REPRODUCED_BYTES=535897
BYTE_IDENTICAL=PASS
```

## Disposable rollback and forward proof

Input was detached implementation commit `5aca7a2`, plus a copied patch below the disposable worktree root.

```text
COMMAND=& scripts\rollback-managed-agent-phase2.ps1 -RepoRoot <DISPOSABLE> -PatchPath <DISPOSABLE>\debug-artifacts\managed-agent-phase2.patch
PATCH_SHA256=D51F97B380F653E0E759B16B92D9511C97229734D16CA3AC2C8573E06C6B06D7
REVERSE_CHECK=PASS
REVERSE_APPLY=PASS
ROLLBACK_BASE=af64499
ROLLBACK_VERIFY=PASS
EXIT=0

COMMAND=<PYTHON> -m pytest tests/test_client_transport.py tests/test_protocol_auth.py tests/test_encryption.py tests/test_dashboard.py -q
......................................................                   [100%]
54 passed in 1.01s
EXIT=0

COMMAND=<PYTHON> -c "import client.PhantomLink; import C2.dashboard; print('ROLLBACK_IMPORT_OK')"
ROLLBACK_IMPORT_OK
EXIT=0

COMMAND=git apply --check debug-artifacts/managed-agent-phase2.patch
EXIT=0
COMMAND=git apply debug-artifacts/managed-agent-phase2.patch
EXIT=0
COMMAND=git diff --quiet 5aca7a2
EXIT=0
COMMAND=git worktree remove --force <DISPOSABLE>
EXIT=0
```

The rollback prints database, CA, TLS, and Phase 1 backup paths for operator archival. It does not remove any database, CA, certificate, key, or backup.

## Preserved pre-existing artifacts

- `debug-artifacts/managed-agent-phase2-preflight/` is retained and added with this evidence.
- `debug-artifacts/managed-agent-preflight/` remains untouched and untracked.
- `debug-artifacts/task4-cli-store/` remains untouched and untracked.
- `debug-artifacts/task4-current.diff` remains untouched and untracked.

## Manual acceptance boundary

- Two Windows machines and private VPN: **PENDING MANUAL ACCEPTANCE**
- Dashboard keystroke observation: **PENDING MANUAL ACCEPTANCE**
- Packet capture proving encrypted heartbeat/identity: **PENDING MANUAL ACCEPTANCE**
- Controller and agent reboot/restart observation: **PENDING MANUAL ACCEPTANCE**
- Exact VPN-only, non-wildcard/non-LAN/non-public reachability from the second machine: **PENDING MANUAL ACCEPTANCE**

Phase 2 retains the runbook's deployability gate until these observations have literal two-machine evidence.
