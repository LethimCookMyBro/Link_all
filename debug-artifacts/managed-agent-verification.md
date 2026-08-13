# Managed Agent Phase 1 Verification

Baseline commit: `fd4c00726d5ac43b47023ec2407d5dd292bb45f9`
Modified commit: `dc02b8b1be193c6ce424e08cae8b31453683a7ca`

## Baseline command

```powershell
G:\for_hack_all\Link_all - Copy\.venv\Scripts\python.exe -m pytest -q
```

Literal output:

```text
........................................................................ [ 38%]
........................................................................ [ 77%]
.........................................                                [100%]
============================== warnings summary ===============================
..\..\.venv\Lib\site-packages\discord\player.py:30
  G:\for_hack_all\Link_all - Copy\.venv\Lib\site-packages\discord\player.py:30: DeprecationWarning: 'audioop' is deprecated and slated for removal in Python 3.13
    import audioop

tests/test_commands_registry.py::TestRegistry::test_every_registered_command_has_metadata
  G:\for_hack_all\Link_all - Copy\.uv-python\cpython-3.12-windows-x86_64-none\Lib\contextlib.py:132: RuntimeWarning: coroutine 'AsyncMockMixin._execute_mock_call' was never awaited
    def __enter__(self):
  Enable tracemalloc to get traceback where the object was allocated.

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
185 passed, 2 warnings in 60.90s (0:01:00)
BASELINE_EXIT=0
```

Exit status: `0`.

## Modified commands

```powershell
G:\for_hack_all\Link_all - Copy\.venv\Scripts\python.exe -m compileall -q client C2
G:\for_hack_all\Link_all - Copy\.venv\Scripts\python.exe -m pytest tests/test_client_transport.py tests/test_agent_config.py tests/test_managed_auth.py tests/test_agent_runtime.py tests/test_agent_logging.py tests/test_agent_runtime_integration.py -q
G:\for_hack_all\Link_all - Copy\.venv\Scripts\python.exe -m pytest -q
```

Literal output:

```text
COMPILE_EXIT=0
........................................................................ [ 35%]
........................................................................ [ 70%]
............................................................             [100%]
204 passed in 20.35s
FOCUSED_EXIT=0
........................................................................ [ 17%]
........................................................................ [ 35%]
........................................................................ [ 53%]
........................................................................ [ 71%]
........................................................................ [ 89%]
..........................................                               [100%]
============================== warnings summary ===============================
..\..\.venv\Lib\site-packages\discord\player.py:30
  G:\for_hack_all\Link_all - Copy\.venv\Lib\site-packages\discord\player.py:30: DeprecationWarning: 'audioop' is deprecated and slated for removal in Python 3.13
    import audioop

tests/test_commands_registry.py::TestMigratedHandlers::test_wifi_prompts_and_interpolates_name
  G:\for_hack_all\Link_all - Copy\.uv-python\cpython-3.12-windows-x86_64-none\Lib\unittest\mock.py:2217: RuntimeWarning: coroutine 'AsyncMockMixin._execute_mock_call' was never awaited
    def __init__(self, name, parent):
  Enable tracemalloc to get traceback where the object was allocated.

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
402 passed, 2 warnings in 81.97s (0:01:21)
FULL_EXIT=0
```

Exit statuses: `0`, `0`, `0`. There were no unhandled thread exception warnings.

## Repeated loopback integration

```powershell
$python = 'G:\for_hack_all\Link_all - Copy\.venv\Scripts\python.exe'
1..3 | ForEach-Object {
  & $python -m pytest tests/test_agent_runtime_integration.py -q --maxfail=1
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
```

Literal output and exit statuses:

```text
RUN=1
...............                                                          [100%]
15 passed in 11.56s
EXIT_STATUS=0
RUN=2
...............                                                          [100%]
15 passed in 10.37s
EXIT_STATUS=0
RUN=3
...............                                                          [100%]
15 passed in 11.43s
EXIT_STATUS=0
```

## Windows logging and pythonw smoke

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_agent_logging.py -q
$pythonw = Resolve-Path .\.venv\Scripts\pythonw.exe
$proc = Start-Process $pythonw -ArgumentList 'client/managed_agent.py','run','--config','debug-artifacts/managed-agent-test.json' -WindowStyle Hidden -PassThru
Start-Sleep -Seconds 3
if (-not $proc.HasExited) { Stop-Process -Id $proc.Id }
Wait-Process -Id $proc.Id -ErrorAction SilentlyContinue
Test-Path debug-artifacts/managed-agent.log
Get-Content debug-artifacts/managed-agent.log | ForEach-Object { $_ | ConvertFrom-Json | Out-Null }
```

Literal output and exit statuses:

```text
.....                                                                    [100%]
5 passed in 0.11s
LOGGING_EXIT=0
PYTHONW_EXITED=True
LOG_EXISTS=True
LOG_JSON_EXIT=0
```

Loopback config path: `debug-artifacts/managed-agent-test.json`

Loopback config SHA-256: `50f1ceeed7c565be05d087357f51c8b547470bd993e631cd466a966dcf5a6d8c`

Managed agent log path: `debug-artifacts/managed-agent.log`

Managed agent log SHA-256: `30b6df8e6de60c614ec3adab8cd34ffc5a226c8a26113c76c311ac583b310852`

No token or credential value is present in this record, config, or log. The smoke credential file was deleted after the process check.

## Patch

Path: `debug-artifacts/managed-agent.patch`

SHA-256: `c006aa9fb1b73da3443ccf2545b2403816742f9e84928514b23cfc386d39416f`

```powershell
$base = (Get-Content debug-artifacts/managed-agent-preflight/feature-base.txt -Raw).Trim()
cmd /d /c "git diff $base..HEAD -- client/transport.py client/agent_config.py client/agent_runtime.py client/agent_logging.py client/managed_agent.py C2/managed_auth.py C2/crypto.py C2/C2.py config.py .env.example tests/test_client_transport.py tests/test_agent_config.py tests/test_managed_auth.py tests/test_agent_runtime.py tests/test_agent_logging.py tests/test_encryption.py > debug-artifacts\managed-agent.patch"
$patch = (Resolve-Path debug-artifacts/managed-agent.patch).Path
$checkRoot = Join-Path 'G:\for_hack_all\Link_all - Copy\.worktrees' "managed-agent-patch-check-$PID"
git worktree add --detach $checkRoot $base
Push-Location $checkRoot
try { git apply --check $patch }
finally { Pop-Location; git worktree remove --force $checkRoot }
```

Literal output and exit status:

```text
PATCH_CHECK_EXIT=0
PATCH_BYTES=225519
```

The non-empty patch applied with `git apply --check` in a clean detached worktree at the baseline commit.

## Dependency, static, and Defender validation

```powershell
$python = 'G:\for_hack_all\Link_all - Copy\.venv\Scripts\python.exe'
uv pip check --python $python
rg -n "av_bypass|av_killer|subprocess|os\.system|CreateProcess|powershell" client/transport.py client/agent_config.py client/agent_runtime.py client/agent_logging.py client/managed_agent.py C2/managed_auth.py
$defender = "$env:ProgramFiles\Windows Defender\MpCmdRun.exe"
$files = 'client/transport.py','client/agent_config.py','client/agent_runtime.py','client/agent_logging.py','client/managed_agent.py','C2/managed_auth.py'
foreach ($file in $files) {
  & $defender -Scan -ScanType 3 -File (Resolve-Path $file).Path -DisableRemediation
  Write-Output "$file DEFENDER_EXIT=$LASTEXITCODE"
}
```

Literal output and exit statuses (`rg` exit `1` means no matches):

```text
PIP_CHECK_EXIT=0
STATIC_FORBIDDEN_HITS=0
client/transport.py DEFENDER_EXIT=0
client/agent_config.py DEFENDER_EXIT=0
client/agent_runtime.py DEFENDER_EXIT=0
client/agent_logging.py DEFENDER_EXIT=0
client/managed_agent.py DEFENDER_EXIT=0
C2/managed_auth.py DEFENDER_EXIT=0
```

Each managed Phase 1 source file reported `found no threats`. A broad scan of the pre-existing `client/` directory returned exit `2` solely because legacy `client/av_bypass.py` was detected; that file is not imported, modified, or included in the managed patch.

## Rollback execution

```powershell
$rollbackRoot = Join-Path 'G:\for_hack_all\Link_all - Copy\.worktrees' "managed-agent-rollback-$PID"
git worktree add --detach $rollbackRoot HEAD
$junction = Join-Path $rollbackRoot '.venv'
New-Item -ItemType Junction -Path $junction -Target 'G:\for_hack_all\Link_all - Copy\.venv' | Out-Null
Push-Location $rollbackRoot
try { powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\rollback-managed-agent.ps1 }
finally {
  Pop-Location
  cmd /d /c "rmdir `"$junction`""
  git worktree remove --force $rollbackRoot
}
```

Literal output and exit status:

```text
.............................                                            [100%]
29 passed in 0.74s
ROLLBACK_EXIT=0
MANAGED_RUNTIME_EXISTS=False
MANAGED_AUTH_EXISTS=False
```

The exact `scripts/rollback-managed-agent.ps1` command completed at exit `0` in a detached disposable worktree. Both managed implementation paths were absent after reverse application, and the legacy listener tests passed.

## Task 7 gate fix round 1

Deterministic RED command:

```powershell
G:\for_hack_all\Link_all - Copy\.venv\Scripts\python.exe -m pytest tests/test_dashboard.py::TestDashboardApp::test_late_refresh_after_teardown_is_contained -q
```

Literal output and exit status:

```text
F                                                                        [100%]
textual.css.query.NoMatches: No nodes match '#clients' on Screen(id='_default')
1 failed in 0.38s
RED_EXIT=1
```

The shared `_refresh` callback now treats only Textual's `NoMatches` lifecycle signal as a late-teardown no-op. Snapshot and widget update errors keep their existing behavior.

Repeated dashboard command:

```powershell
1..30 | ForEach-Object {
  G:\for_hack_all\Link_all - Copy\.venv\Scripts\python.exe -m pytest tests/test_dashboard.py -q
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
```

Literal output and exit status:

```text
30 consecutive runs: 13 passed
DASHBOARD_REPEAT_30_EXIT=0
```

Runbook config encoding/load command:

```powershell
$python = 'G:\for_hack_all\Link_all - Copy\.venv\Scripts\python.exe'
$configPath = Join-Path (Resolve-Path debug-artifacts).Path 'runbook-config-check.json'
$pin = '0' * 64
$configJson = @{
  controller_host='127.0.0.1'; managed_port=5443; enrollment_port=5444
  tls_cert_sha256=$pin; connect_timeout=0.5; io_poll_interval=0.5
  controller_ping_interval=30; controller_pong_timeout=10; agent_read_deadline=90
  retry_base=1; retry_max=30; retry_jitter=0.2
  log_path=(Join-Path (Resolve-Path debug-artifacts).Path 'managed-agent.log')
  log_max_bytes=1048576; log_backup_count=5
} | ConvertTo-Json
[IO.File]::WriteAllText($configPath, $configJson, [Text.UTF8Encoding]::new($false))
Remove-Variable configJson
@'
from pathlib import Path
from client.agent_config import _apply_private_acl, load_config
path=Path('debug-artifacts/runbook-config-check.json')
_apply_private_acl(path)
config=load_config(path)
print(f'CONFIG_VALID={config.controller_host}:{config.managed_port}')
print(f'BOM_PRESENT={path.read_bytes().startswith(bytes.fromhex("efbbbf"))}')
path.unlink()
'@ | & $python -
```

Literal output and exit status:

```text
CONFIG_VALID=127.0.0.1:5443
BOM_PRESENT=False
RUNBOOK_CONFIG_EXIT=0
```

Fresh Task 7 and full-gate commands:

```powershell
1..3 | ForEach-Object {
  G:\for_hack_all\Link_all - Copy\.venv\Scripts\python.exe -m pytest tests/test_agent_runtime_integration.py -q --maxfail=1
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
G:\for_hack_all\Link_all - Copy\.venv\Scripts\python.exe -m pytest tests/test_client_transport.py tests/test_agent_config.py tests/test_managed_auth.py tests/test_agent_runtime.py tests/test_agent_logging.py tests/test_agent_runtime_integration.py -q
G:\for_hack_all\Link_all - Copy\.venv\Scripts\python.exe -m pytest -q
```

Literal output and exit statuses:

```text
INTEGRATION_RUN=1: 15 passed in 11.82s; INTEGRATION_EXIT=0
INTEGRATION_RUN=2: 15 passed in 12.90s; INTEGRATION_EXIT=0
INTEGRATION_RUN=3: 15 passed in 11.82s; INTEGRATION_EXIT=0
204 passed in 19.48s
FOCUSED_EXIT=0
403 passed, 2 warnings in 93.53s (0:01:33)
FULL_FRESH_EXIT=0
```

No unhandled thread exception warning appeared.

## All new or modified paths

- `.env.example`
- `C2/C2.py`
- `C2/crypto.py`
- `C2/managed_auth.py`
- `client/PhantomLink.py`
- `client/agent_config.py`
- `client/agent_logging.py`
- `client/agent_runtime.py`
- `client/managed_agent.py`
- `client/transport.py`
- `config.py`
- `requirements.txt`
- `tests/test_agent_config.py`
- `tests/test_agent_logging.py`
- `tests/test_agent_runtime.py`
- `tests/test_client_transport.py`
- `tests/test_encryption.py`
- `tests/test_managed_auth.py`
- `tests/test_phantomlink_config_import.py`
- `tests/test_protocol_auth.py`
- `tests/test_agent_runtime_integration.py`
- `docs/runbooks/managed-agent-phase1.md`
- `scripts/rollback-managed-agent.ps1`
- `debug-artifacts/managed-agent.patch`
- `debug-artifacts/managed-agent-verification.md`

## Final whole-branch fix wave

Implementation commits before artifact finalization: `1b32aa3e785907e6a6806f407d11451aed1ef92c` and `ebc0708d424cbce3ce00f2fd1cdd9e0f45f1c6a2`.

### Deterministic RED evidence

The independent review reproduced five failures before this wave:

- managed/enrollment constructors received legacy `HOST` (`0.0.0.0`) even when the runbook set `PHANTOMLINK_HOST`;
- independent store instances/processes could perform stale read-modify-write and lose token updates or resurrect TOKEN_A;
- unauthenticated connections created one non-daemon worker each without admission limit;
- reverse rollback removed `client/transport.py` but the patch omitted dependent `client/PhantomLink.py`, breaking its import;
- `KeyboardInterrupt` propagated from `AgentRuntime.run()` rather than requesting stop and returning exit 0.

New deterministic regressions exercise loopback-only managed host validation and constructor arguments, six spawned processes issuing 30 tokens without loss, concurrent TOKEN_A consume/issue without replay, managed and enrollment caps with eight stalled unauthenticated sockets, bounded shutdown/no live owners, and Ctrl+C stop plus logging flush.

### Targeted command and literal output

`powershell
1..3 | ForEach-Object {
  G:\for_hack_all\Link_all - Copy\.venv\Scripts\python.exe -m pytest tests/test_managed_auth.py tests/test_agent_config.py -q --maxfail=1
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
`

`	ext
TARGET_RUN=1: 108 passed in 10.31s; exit 0
TARGET_RUN=2: 108 passed in 9.82s; exit 0
TARGET_RUN=3: 108 passed in 10.45s; exit 0
`

### Repeated integration, compile, focused and full gates

`powershell
1..3 | ForEach-Object {
  G:\for_hack_all\Link_all - Copy\.venv\Scripts\python.exe -m pytest tests/test_agent_runtime_integration.py -q --maxfail=1
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
G:\for_hack_all\Link_all - Copy\.venv\Scripts\python.exe -m compileall -q client C2
G:\for_hack_all\Link_all - Copy\.venv\Scripts\python.exe -m pytest tests/test_client_transport.py tests/test_agent_config.py tests/test_managed_auth.py tests/test_agent_runtime.py tests/test_agent_logging.py tests/test_agent_runtime_integration.py -q
G:\for_hack_all\Link_all - Copy\.venv\Scripts\python.exe -m pytest -q
`

`	ext
INTEGRATION_RUN=1: 15 passed in 12.74s; exit 0
INTEGRATION_RUN=2: 15 passed in 11.50s; exit 0
INTEGRATION_RUN=3: 15 passed in 13.02s; exit 0
COMPILE_EXIT=0
210 passed in 24.16s
FOCUSED_EXIT=0
409 passed, 2 warnings in 105.22s (0:01:45)
FULL_EXIT=0
`

No unhandled thread exception warning appeared.

### Dependency-complete patch and rollback

Patch command includes the complete branch delta needed by the managed feature, including `client/PhantomLink.py`, while excluding the patch/verification/runbook/rollback/integration artifacts themselves.

`powershell
$base=(Get-Content debug-artifacts\managed-agent-preflight\feature-base.txt -Raw).Trim()
cmd /d /c "git diff $base..HEAD -- . :(exclude)debug-artifacts/managed-agent.patch :(exclude)debug-artifacts/managed-agent-verification.md :(exclude)docs/runbooks/managed-agent-phase1.md :(exclude)scripts/rollback-managed-agent.ps1 :(exclude)tests/test_agent_runtime_integration.py > debug-artifacts\managed-agent.patch"
`

`	ext
PATCH_CHECK_EXIT=0
PATCH_BYTES=225519
PATCH_SHA256=c006aa9fb1b73da3443ccf2545b2403816742f9e84928514b23cfc386d39416f
PHANTOMLINK_PATH_INCLUDED=True
`

Exact rollback script execution in a detached disposable worktree:

`	ext
23 passed in 0.08s
6 passed in 0.77s
ROLLBACK_EXIT=0
TRANSPORT_EXISTS=False
PHANTOM_IMPORT_PATCH_REVERSED=True
`

The script successfully imported `client.PhantomLink` after reverse application before running the legacy protocol and listener suites separately.

### Defender and dependency validation

`	ext
client/transport.py DEFENDER_EXIT=0
client/agent_config.py DEFENDER_EXIT=0
client/agent_runtime.py DEFENDER_EXIT=0
client/agent_logging.py DEFENDER_EXIT=0
client/managed_agent.py DEFENDER_EXIT=0
C2/managed_auth.py DEFENDER_EXIT=0
PIP_CHECK_EXIT=0
`

Every managed source reported no threats; all 51 installed packages were compatible. No token or credential values were logged or added to artifacts.


## Mechanical final-HEAD artifact refresh

Regenerated from final implementation HEAD `47e1ce1` with the recorded dependency-complete exclusions.

```powershell
$base=(Get-Content debug-artifacts\managed-agent-preflight\feature-base.txt -Raw).Trim()
cmd /d /c "git diff $base..HEAD -- . :(exclude)debug-artifacts/managed-agent.patch :(exclude)debug-artifacts/managed-agent-verification.md :(exclude)docs/runbooks/managed-agent-phase1.md :(exclude)scripts/rollback-managed-agent.ps1 :(exclude)tests/test_agent_runtime_integration.py > debug-artifacts\managed-agent.patch"
git apply --reverse --check debug-artifacts\managed-agent.patch
```

```text
PATCH_GENERATE_EXIT=0
PATCH_SHA256=c006aa9fb1b73da3443ccf2545b2403816742f9e84928514b23cfc386d39416f
PATCH_BYTES=225519
PHANTOMLINK_PATH_INCLUDED=True
MANAGED_AUTH_TEST_PATH_INCLUDED=True
REVERSE_CHECK_EXIT=0
```

Forward clean-base check:

```text
FORWARD_CHECK_EXIT=0
```

Exact rollback script in a disposable worktree using the regenerated patch:

```text
23 passed in 0.08s
6 passed in 0.81s
ROLLBACK_EXIT=0
TRANSPORT_EXISTS=False
MANAGED_AUTH_EXISTS=False
MANAGED_AGENT_EXISTS=False
PHANTOMLINK_IMPORT=OK
POST_IMPORT_EXIT=0
```
