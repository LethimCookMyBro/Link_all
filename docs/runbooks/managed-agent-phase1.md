# Managed Agent Phase 1 Runbook

All listener and agent addresses in this procedure are loopback-only. Run from the repository root in PowerShell.

## 1. Prepare the operator certificate and calculate its pin

Place an RSA key and matching PEM certificate at `debug-artifacts/managed-key.pem` and `debug-artifacts/managed-cert.pem`. Calculate the DER SHA-256 pin exactly as follows:

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

Capture the same value without copying it into shell history:

```powershell
$python = (Resolve-Path .\.venv\Scripts\python.exe).Path
$pin = @'
from pathlib import Path
from hashlib import sha256
from cryptography import x509
from cryptography.hazmat.primitives.serialization import Encoding
cert = x509.load_pem_x509_certificate(Path("debug-artifacts/managed-cert.pem").read_bytes())
print(sha256(cert.public_bytes(Encoding.DER)).hexdigest())
'@ | & $python -
$pin = $pin.Trim()
```

## 2. Set the five managed/TLS values and start the loopback controller

```powershell
$env:PHANTOMLINK_MANAGED_HOST = '127.0.0.1'
$env:PHANTOMLINK_MANAGED_PORT = '5443'
$env:PHANTOMLINK_ENROLLMENT_PORT = '5444'
$env:PHANTOMLINK_TLS_CERT = (Resolve-Path debug-artifacts\managed-cert.pem).Path
$env:PHANTOMLINK_TLS_KEY = (Resolve-Path debug-artifacts\managed-key.pem).Path
$env:PHANTOMLINK_MANAGED_STORE = (New-Item -ItemType Directory -Force debug-artifacts\managed-store).FullName
& $python C2/C2.py
```

Keep that window open. Startup must identify both listeners on `127.0.0.1`.

## 3. Issue one 600-second token and write the non-secret JSON config

In a second PowerShell window:

```powershell
$python = (Resolve-Path .\.venv\Scripts\python.exe).Path
$pin = @'
from pathlib import Path
from hashlib import sha256
from cryptography import x509
from cryptography.hazmat.primitives.serialization import Encoding
cert = x509.load_pem_x509_certificate(Path("debug-artifacts/managed-cert.pem").read_bytes())
print(sha256(cert.public_bytes(Encoding.DER)).hexdigest())
'@ | & $python -
$pin = $pin.Trim()
$store = (Resolve-Path debug-artifacts\managed-store).Path
$tokenFile = Join-Path (Resolve-Path debug-artifacts).Path 'managed-enrollment-token.txt'
$token = & $python -m C2.managed_auth issue-token --store $store --ttl 600
if ($LASTEXITCODE -ne 0) { throw "Token issue failed: $LASTEXITCODE" }
[IO.File]::WriteAllText($tokenFile, $token.Trim(), [Text.UTF8Encoding]::new($false))
Remove-Variable token
@'
from pathlib import Path
from client.agent_config import _apply_private_acl
_apply_private_acl(Path("debug-artifacts/managed-enrollment-token.txt"))
'@ | & $python -

$configPath = Join-Path (Resolve-Path debug-artifacts).Path 'managed-agent-test.json'
$configJson = @{
    controller_host = '127.0.0.1'
    managed_port = 5443
    enrollment_port = 5444
    tls_cert_sha256 = $pin
    connect_timeout = 0.5
    io_poll_interval = 0.5
    controller_ping_interval = 30
    controller_pong_timeout = 10
    agent_read_deadline = 90
    retry_base = 1
    retry_max = 30
    retry_jitter = 0.2
    log_path = (Join-Path (Resolve-Path debug-artifacts).Path 'managed-agent.log')
    log_max_bytes = 1048576
    log_backup_count = 5
} | ConvertTo-Json
[IO.File]::WriteAllText($configPath, $configJson, [Text.UTF8Encoding]::new($false))
Remove-Variable configJson
@'
from pathlib import Path
from client.agent_config import _apply_private_acl, load_config
path = Path("debug-artifacts/managed-agent-test.json")
_apply_private_acl(path)
load_config(path)
print("CONFIG_VALID")
'@ | & $python -
```

The config contains no token or credential. Do not print the token. The next command deletes its token file before making the HTTPS request.

## 4. Enroll once, run, and perform the pythonw smoke

```powershell
& $python client/managed_agent.py enroll --config $configPath --token-file $tokenFile
if ($LASTEXITCODE -ne 0) { throw "Enrollment failed: $LASTEXITCODE" }
Test-Path $tokenFile # expected: False
& $python client/managed_agent.py run --config $configPath # Ctrl+C requests a clean stop and exits 0
```

Stop the foreground run with Ctrl+C after authenticated lifecycle events, then run:

```powershell
$pythonw = Resolve-Path .\.venv\Scripts\pythonw.exe
$proc = Start-Process $pythonw -ArgumentList 'client/managed_agent.py','run','--config','debug-artifacts/managed-agent-test.json' -WindowStyle Hidden -PassThru
Start-Sleep -Seconds 3
Stop-Process -Id $proc.Id
Wait-Process -Id $proc.Id -ErrorAction SilentlyContinue
Test-Path debug-artifacts/managed-agent.log
Get-Content debug-artifacts/managed-agent.log | ForEach-Object { $_ | ConvertFrom-Json | Out-Null }
```

Expected: no console window, the lifecycle log exists, and every line parses as JSON. Neither config nor log may contain token or credential values.

## 5. Verification and rollback

Run every gate in `debug-artifacts/managed-agent-verification.md`. Validate rollback only in a disposable worktree:

```powershell
.\scripts\rollback-managed-agent.ps1
```

It reverse-applies `debug-artifacts/managed-agent.patch` and runs the legacy listener tests.
