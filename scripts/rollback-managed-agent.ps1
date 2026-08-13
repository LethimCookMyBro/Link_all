param()
$patch = Join-Path $PSScriptRoot '..\debug-artifacts\managed-agent.patch'
git apply --reverse --check $patch
if ($LASTEXITCODE -ne 0) { throw 'Rollback preflight failed' }
git apply --reverse $patch
if ($LASTEXITCODE -ne 0) { throw 'Rollback apply failed' }
.\.venv\Scripts\python.exe -c "import client.PhantomLink"
if ($LASTEXITCODE -ne 0) { throw 'Legacy client import failed after rollback' }
.\.venv\Scripts\python.exe -m pytest tests/test_protocol_auth.py tests/test_c2_coverage.py -q
if ($LASTEXITCODE -ne 0) { throw 'Legacy verification failed after rollback' }
