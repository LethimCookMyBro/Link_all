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

SHA-256: `52fb0202daffb458dcedeb937e40cd5104042a781600ee30acbfc1ecc2693ed5`

```text
PATCH_CHECK_EXIT=0
PATCH_BYTES=206205
```

The non-empty patch applied with `git apply --check` in a clean detached worktree at the baseline commit.

## Dependency, static, and Defender validation

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

```text
.............................                                            [100%]
29 passed in 0.74s
ROLLBACK_EXIT=0
MANAGED_RUNTIME_EXISTS=False
MANAGED_AUTH_EXISTS=False
```

The exact `scripts/rollback-managed-agent.ps1` command completed at exit `0` in a detached disposable worktree. Both managed implementation paths were absent after reverse application, and the legacy listener tests passed.

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
