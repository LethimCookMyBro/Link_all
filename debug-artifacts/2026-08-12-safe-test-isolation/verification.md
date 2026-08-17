# Verification record

## Scope

Local Windows test/lifecycle defects only. No remote service was contacted during validation; network-facing behavior was mocked.

## Baseline breadcrumbs

1. `python -m pytest -q` -> exit `1`: configured Python 3.11 interpreter was missing.
2. Fresh Python 3.12 environment + `requirements.txt` -> exit `1`: eight collection errors because `nacl` was imported but undeclared.
3. After transiently installing `PyNaCl` -> exit `1`: `195 passed, 2 failed`; both failures were undeclared `textual` imports.
4. After transiently installing `textual` -> exit `0`: `197 passed`, with an unawaited coroutine warning.
5. Strict full run -> exit `1`: `1 failed, 196 passed`; dashboard timer raised `NoMatches: #clients`; warnings also identified an unawaited mocked coroutine and a real background keylogger write.
6. Dashboard loop before fix -> exit `1`: literal output `dashboard_repro_failures=1/20`.
7. New regression test before fix -> exit `1`: `TestDashboardApp.test_late_refresh_after_shutdown_is_ignored` raised `NoMatches`.

## Modified behavior

- A clean install from `requirements.txt` includes `PyNaCl` and `textual`.
- Dashboard refresh callbacks return after shutdown or when lifecycle widgets no longer exist.
- Launcher test passes a plain sentinel to the mocked `asyncio.run`, so no coroutine is abandoned.
- Startup-sequence test mocks both background `start()` methods, so tests do not start keylogging or screenshot threads.

## Final commands and literal outputs

### Targeted isolation checks

Command:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_combined_launcher.py tests/test_phantomlink_deep_coverage.py -q -W error::pytest.PytestUnraisableExceptionWarning -W error::pytest.PytestUnhandledThreadExceptionWarning
```

Output: `6 passed, 1 warning in 8.33s`; exit `0`. The remaining warning is the third-party Python 3.12 `audioop` deprecation.

### Dashboard regression and stress

Command:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_dashboard.py -q -W error::pytest.PytestUnraisableExceptionWarning -W error::pytest.PytestUnhandledThreadExceptionWarning
```

Output: `13 passed in 0.50s`; exit `0`.

Stress output: `dashboard_repro_failures_after=0/30`; exit `0`.

### Full strict suite

Command:

```powershell
.\.venv\Scripts\python.exe -m compileall -q -x '[\\/](build|dist|\.venv|\.uv-python|\.uv-cache)[\\/]' .
.\.venv\Scripts\python.exe -m pytest -q -W error::pytest.PytestUnraisableExceptionWarning -W error::pytest.PytestUnhandledThreadExceptionWarning
```

Output: `198 passed, 1 warning in 52.57s`; exit `0`.

### Clean environment from requirements

Command:

```powershell
uv venv .venv-verify --python 3.12
uv pip install --python .venv-verify\Scripts\python.exe -r requirements.txt pytest
.\.venv-verify\Scripts\python.exe -c "import nacl,textual; print('dependency_imports=PASS', nacl.__version__, textual.__version__)"
.\.venv-verify\Scripts\python.exe -m pytest tests -q -W error::pytest.PytestUnraisableExceptionWarning -W error::pytest.PytestUnhandledThreadExceptionWarning
```

Output: `dependency_imports=PASS 1.6.2 8.2.8` and `198 passed, 1 warning in 19.18s`; exit `0`. The temporary environment was removed after verification.

### Patch and rollback

- `git apply --check -R -- debug-artifacts\2026-08-12-safe-test-isolation\changes.patch` -> `reverse_patch_check=PASS`; exit `0`.
- Staged execution of `rollback.ps1` restored all five original hashes and removed the staged runbook; output `rollback_execution=PASS`; exit `0`.

## SHA-256

| Role | File | SHA-256 |
|---|---|---|
| Original | `requirements.txt.original` | `863874BD0A9D1E537D5AF95BD091A7EA74B8B8392BE7B07D81FACD6DB649422C` |
| Original | `dashboard.py.original` | `2D8D3979E87756CACA08F6E33956574823AB3EC55141A89C76A859EDA8F427B5` |
| Original | `test_dashboard.py.original` | `FBE92BED8D7B1DB1C9D73789AF80B0AACA7867B86582DA6D757096A6D82DFFAF` |
| Original | `test_combined_launcher.py.original` | `6E44BEBB44F9DDD8F0189DEBB108D74E9EA4989D3B8B2FC81728A49A6E3DBD6E` |
| Original | `test_phantomlink_deep_coverage.py.original` | `3A0B7534E009A928459F240AF7F604DE8A0DFF7F0AAA328F693C13C4BC3C29DA` |
| Modified | `requirements.txt` | `96D516F6F2CB643ED69B86C6290B28A6535DFA13EB6E6437FF5C4574C1D6B6FF` |
| Modified | `C2/dashboard.py` | `B98EEF940F9EEF2169EBCACEF34281C2EBFF304BC3020B0DE8FC39729616B50D` |
| Modified | `tests/test_dashboard.py` | `0C2FC2FA89C525F358118056CDD95F47A15BC7DEECA292BF06368689E879052B` |
| Modified | `tests/test_combined_launcher.py` | `FCF3DE01121F54A3532F4DF1BB27BF054CBA8330A1B1AA9209BC13A0441D1172` |
| Modified | `tests/test_phantomlink_deep_coverage.py` | `B16B2CB1BCBEA4EBCCC21AF1448F4E851019655A3DB461930AC6DC1587EA9EF6` |

## Gates

- Test suite: pass.
- Repro error rate: `1/20` before, `0/30` after.
- Latency: not applicable to an in-process lifecycle/test-isolation fix.
- Edge cases: late shutdown callback, broken snapshot, mocked async entry point, and background startup reviewed by the strict suite.
