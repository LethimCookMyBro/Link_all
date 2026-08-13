# Managed Agent Phase 2 Private-Network Runbook

## Scope and acceptance state

PhantomLink only binds its managed TLS and enrollment listeners to an already-configured private VPN address. It does not install, configure, or change the VPN. Run each block locally on the named machine; this runbook contains no remote-execution commands.

- Two-machine result: **PENDING MANUAL ACCEPTANCE**
- Packet-capture result: **PENDING MANUAL ACCEPTANCE**

The examples use controller VPN IP `10.8.0.1`, agent VPN IP `10.8.0.2`, managed port `5443`, and enrollment port `5444`.

## 1. Prove the external VPN first

Controller machine:

```powershell
$ControllerVpnIp = '10.8.0.1'
$AgentVpnIp = '10.8.0.2'
$ControllerAddress = Get-NetIPAddress -AddressFamily IPv4 -IPAddress $ControllerVpnIp -ErrorAction Stop
$ControllerAddress | Select-Object IPAddress,InterfaceAlias,AddressState,PrefixOrigin
if (-not (Test-Connection -ComputerName $AgentVpnIp -Count 2 -Quiet)) { throw 'agent VPN IP unreachable' }
'VPN_REACHABILITY=PASS'
```

Expected automated output: one address row exactly `10.8.0.1` with `AddressState` `Preferred`, followed by `VPN_REACHABILITY=PASS`.

Agent machine:

```powershell
$ControllerVpnIp = '10.8.0.1'
$AgentVpnIp = '10.8.0.2'
$AgentAddress = Get-NetIPAddress -AddressFamily IPv4 -IPAddress $AgentVpnIp -ErrorAction Stop
$AgentAddress | Select-Object IPAddress,InterfaceAlias,AddressState,PrefixOrigin
if (-not (Test-Connection -ComputerName $ControllerVpnIp -Count 2 -Quiet)) { throw 'controller VPN IP unreachable' }
'VPN_REACHABILITY=PASS'
```

Expected automated output: one address row exactly `10.8.0.2` with `AddressState` `Preferred`, followed by `VPN_REACHABILITY=PASS`. If either address is absent or a ping fails, stop and repair the external VPN outside PhantomLink.

## 2. Prepare controller state and certificates

Run locally on the controller:

```powershell
Set-Location 'G:\for_hack_all\Link_all - Copy'
$Python = (Resolve-Path '.\.venv\Scripts\python.exe').Path
$ControllerVpnIp = '10.8.0.1'
$ManagedPort = 5443
$EnrollmentPort = 5444
$Store = (New-Item -ItemType Directory -Force '.\managed-store').FullName
$Db = Join-Path $Store 'managed.db'
$CaCert = Join-Path $Store 'ca.crt'
$CaKey = Join-Path $Store 'ca.key.dpapi'
$ServerCert = Join-Path $Store 'server.crt'
$ServerKey = Join-Path $Store 'server.key'

& $Python -m C2.managed_auth init-ca --ca-key $CaKey --ca-cert $CaCert --common-name 'PhantomLink Managed CA'
if ($LASTEXITCODE -ne 0) { throw "init-ca exit code $LASTEXITCODE" }
```

Expected output is `CA initialized` and exit code `0`, both for creation and the same already-initialized CA. No key content is printed.

Place an externally provisioned PEM server-auth certificate and matching private key at the exact paths below. Its identity/SAN must cover `10.8.0.1`.

```powershell
$ServerCertSource = 'C:\vpn-pki\phantomlink-server.crt'
$ServerKeySource = 'C:\vpn-pki\phantomlink-server.key'
Copy-Item -LiteralPath $ServerCertSource -Destination $ServerCert -Force
Copy-Item -LiteralPath $ServerKeySource -Destination $ServerKey -Force
icacls $ServerKey /inheritance:r | Out-Null
icacls $ServerKey /grant:r "${env:USERDOMAIN}\${env:USERNAME}:(F)" | Out-Null
@($CaCert,$CaKey,$ServerCert,$ServerKey) | ForEach-Object {
    if (-not (Test-Path -LiteralPath $_ -PathType Leaf)) { throw "missing file: $_" }
}
'CERTIFICATE_PATHS=READY'
```

Expected output: `CERTIFICATE_PATHS=READY`.

Configure the same controller shell. Wildcards (`0.0.0.0`, `::`) and LAN/public addresses are prohibited.

```powershell
if ($ControllerVpnIp -in @('0.0.0.0','::')) { throw 'wildcard managed bind prohibited' }
if ((Get-NetIPAddress -IPAddress $ControllerVpnIp -ErrorAction Stop).IPAddress -ne $ControllerVpnIp) {
    throw 'exact controller VPN address is not local'
}
$env:PHANTOMLINK_MANAGED_HOST = $ControllerVpnIp
$env:PHANTOMLINK_MANAGED_PORT = "$ManagedPort"
$env:PHANTOMLINK_ENROLLMENT_PORT = "$EnrollmentPort"
$env:PHANTOMLINK_MANAGED_DB = $Db
$env:PHANTOMLINK_MANAGED_STORE = $Store
$env:PHANTOMLINK_CA_CERT = $CaCert
$env:PHANTOMLINK_CA_KEY = $CaKey
$env:PHANTOMLINK_TLS_CERT = $ServerCert
$env:PHANTOMLINK_TLS_KEY = $ServerKey
& $Python -c "import config; print('PHASE2_CONFIG=' + ('READY' if config.managed_phase2_enabled() else 'INCOMPLETE'))"
```

Expected output: `PHASE2_CONFIG=READY`. A partial configuration emits the single controller error `Managed Phase 2 configuration incomplete; managed listeners not started` and starts neither managed listener.

## 3. Start and prove the exact listeners

```powershell
& $Python -m C2.C2
```

Expected startup line:

```text
[+] Managed TLS on 10.8.0.1:5443; enrollment HTTPS on 10.8.0.1:5444
```

In a second local controller window:

```powershell
$ControllerVpnIp = '10.8.0.1'
$ManagedPort = 5443
$EnrollmentPort = 5444
$ManagedListeners = Get-NetTCPConnection -State Listen |
    Where-Object LocalPort -in @($ManagedPort,$EnrollmentPort) |
    Sort-Object LocalPort | Select-Object LocalAddress,LocalPort,State
$ManagedListeners | Format-Table -AutoSize
if (@($ManagedListeners).Count -ne 2) { throw 'expected exactly two managed listeners' }
if ($ManagedListeners.Where({ $_.LocalAddress -ne $ControllerVpnIp }).Count) {
    throw 'managed listener escaped exact VPN bind'
}
'LISTENER_BIND=PASS'
```

Expected output ends with:

```text
LocalAddress LocalPort State
------------ --------- -----
10.8.0.1          5443 Listen
10.8.0.1          5444 Listen
LISTENER_BIND=PASS
```

## 4. Issue a BOM-free private token file

On the controller:

```powershell
$TokenFile = Join-Path $env:TEMP 'phantomlink-enrollment-token.txt'
$Token = & $Python -m C2.managed_auth issue-token --db $env:PHANTOMLINK_MANAGED_DB --ttl 600
if ($LASTEXITCODE -ne 0 -or @($Token).Count -ne 1 -or [string]::IsNullOrWhiteSpace($Token)) {
    throw 'token issue failed'
}
[IO.File]::WriteAllText($TokenFile,[string]$Token,[Text.UTF8Encoding]::new($false))
icacls $TokenFile /inheritance:r | Out-Null
icacls $TokenFile /grant:r "${env:USERDOMAIN}\${env:USERNAME}:(F)" | Out-Null
& $Python -c "from pathlib import Path; p=Path(r'$TokenFile'); b=p.read_bytes(); assert not b.startswith(bytes((239,187,191))); print('TOKEN_FILE=BOM_FREE')"
& $Python -c "from client.agent_config import validate_private_file; validate_private_file(r'$TokenFile'); print('TOKEN_ACL=PRIVATE')"
$Token = $null
```

Expected output:

```text
TOKEN_FILE=BOM_FREE
TOKEN_ACL=PRIVATE
```

Transfer the file once through the operator-approved channel outside PhantomLink. Never copy its content into chat, logs, shell history, or this runbook.

## 5. Configure, pin, enroll, run, and stop the agent

On the agent, place the transferred token at `C:\ProgramData\PhantomLink\enrollment-token.txt` and the public server certificate (never the private key) at `C:\vpn-pki\phantomlink-server.crt`, then run:

```powershell
Set-Location 'G:\for_hack_all\Link_all - Copy'
$Python = (Resolve-Path '.\.venv\Scripts\python.exe').Path
$ControllerVpnIp = '10.8.0.1'
$ManagedPort = 5443
$EnrollmentPort = 5444
$AgentRoot = New-Item -ItemType Directory -Force 'C:\ProgramData\PhantomLink'
$ConfigPath = Join-Path $AgentRoot.FullName 'managed-agent.json'
$TokenFile = Join-Path $AgentRoot.FullName 'enrollment-token.txt'
$CertificateStore = Join-Path $AgentRoot.FullName 'managed-identity.dpapi'
$LogPath = Join-Path $AgentRoot.FullName 'managed-agent.log'
$ControllerLeaf = [Security.Cryptography.X509Certificates.X509Certificate2]::new('C:\vpn-pki\phantomlink-server.crt')
$Pin = $ControllerLeaf.GetCertHashString([Security.Cryptography.HashAlgorithmName]::SHA256).ToLowerInvariant()
if ($Pin -notmatch '^[0-9a-f]{64}$') { throw 'invalid server certificate pin' }
$Config = [ordered]@{
    controller_host = $ControllerVpnIp
    managed_port = $ManagedPort
    enrollment_port = $EnrollmentPort
    tls_cert_sha256 = $Pin
    display_name = $env:COMPUTERNAME
    agent_version = '2.0'
    certificate_store_path = $CertificateStore
    log_path = $LogPath
}
[IO.File]::WriteAllText($ConfigPath,($Config | ConvertTo-Json),[Text.UTF8Encoding]::new($false))
foreach ($PrivateFile in @($ConfigPath,$TokenFile)) {
    icacls $PrivateFile /inheritance:r | Out-Null
    icacls $PrivateFile /grant:r "${env:USERDOMAIN}\${env:USERNAME}:(F)" | Out-Null
}
& $Python -c "from client.agent_config import load_config; load_config(r'$ConfigPath'); print('AGENT_CONFIG=VALID')"
```

Expected output: `AGENT_CONFIG=VALID`.

Enroll; the token file is deleted after its read attempt:

```powershell
& $Python -m client.managed_agent enroll --config $ConfigPath --token-file $TokenFile
$EnrollExit = $LASTEXITCODE
"ENROLL_EXIT=$EnrollExit"
"TOKEN_REMAINS=$(Test-Path -LiteralPath $TokenFile)"
if ($EnrollExit -ne 0 -or (Test-Path -LiteralPath $TokenFile)) { throw 'enrollment failed' }
```

Expected output:

```text
ENROLL_EXIT=0
TOKEN_REMAINS=False
```

Run in the foreground:

```powershell
& $Python -m client.managed_agent run --config $ConfigPath
```

Expected behavior: the process remains active and the dashboard changes the device `OFFLINE` -> `ONLINE`. Press `Ctrl+C` once for a clean stop; expected exit code is `0`.

## 6. Dashboard D/R/F/Q acceptance

1. Press `m` to open **Managed Agents**.
2. Press `f`; filter by display name or agent ID. Only matching rows should remain.
3. Select the online device, press `d`, then `y`. Expected: `ONLINE` -> `OFFLINE`; a running agent automatically reconnects to `ONLINE`.
4. Press `r`; enter the first 8 characters (short ID) of the selected agent ID and a non-empty reason. Expected: `ONLINE`/`OFFLINE` -> `REVOKED`, live session closed immediately by the in-process action, and later authentication rejected.
5. Press `q` to stop the dashboard. Controller cleanup also bounded-joins its dashboard thread.

Human observation is **PENDING MANUAL ACCEPTANCE**.

Local CLI display checks:

```powershell
& $Python -m C2.managed_auth list-devices --db $env:PHANTOMLINK_MANAGED_DB
if ($LASTEXITCODE -ne 0) { throw 'list-devices failed' }
& $Python -m C2.managed_auth list-audit --db $env:PHANTOMLINK_MANAGED_DB --limit 20
if ($LASTEXITCODE -ne 0) { throw 'list-audit failed' }
```

Expected output: compact JSON arrays containing display-safe values and no certificate PEM, token digest, key content, or DPAPI data.

## 7. Revoke, renewal, and unavailable signer

Replace the example with an agent UUID returned by `list-devices`:

```powershell
$AgentId = '00000000-0000-4000-8000-000000000001'
& $Python -m C2.managed_auth revoke --db $env:PHANTOMLINK_MANAGED_DB --agent-id $AgentId --actor $env:USERNAME --reason 'acceptance revoke'
"REVOKE_EXIT=$LASTEXITCODE"
```

Expected output for an existing device ends with `REVOKE_EXIT=0`. Revoke is idempotent; unknown IDs exit `1`, invalid arguments exit `2`, and repository failures exit `5`.

There is deliberately no separate-process CLI disconnect command. Dashboard `D` is the only live disconnect surface because it uses the controller's in-process `DeviceActionService` and owns the live session socket. The CLI revoke above is durable: if the device is already connected, the controller rejects and closes that session at its next durable heartbeat authorization check. Dashboard `R` performs the same durable revoke and immediately closes the in-process live session.

Renewal is automatic with 30 days or less remaining. Use a deliberately short-lived acceptance certificate or wait for the renewal window; never change a production clock. Expected: `CERTIFICATE_RENEWED` in audit data with the same agent ID. Result: **PENDING MANUAL ACCEPTANCE**.

To prove signing-key-unavailable behavior without exposing or deleting the key, leave the controller running and temporarily rename only the protected key:

```powershell
$UnavailableKey = "$env:PHANTOMLINK_CA_KEY.unavailable"
Move-Item -LiteralPath $env:PHANTOMLINK_CA_KEY -Destination $UnavailableKey
# Trigger/wait for renewal from the short-lived acceptance agent.
Move-Item -LiteralPath $UnavailableKey -Destination $env:PHANTOMLINK_CA_KEY
```

Expected: enrollment/renewal signing is rejected while unavailable; a still-valid existing certificate remains usable, and failed signing does not consume a token. Restore the key immediately. Result: **PENDING MANUAL ACCEPTANCE**.

## 8. Restart and automatic reconnect

Leave the enrolled, non-revoked agent running. On the controller press `Ctrl+C`, wait for `[+] Cleaning up...`, and manually run `& $Python -m C2.C2` again in the prepared shell. Do not re-enroll or issue another token. Expected: the same device returns to `ONLINE` automatically.

```powershell
& $Python -m C2.managed_auth list-devices --db $env:PHANTOMLINK_MANAGED_DB
"LIST_EXIT=$LASTEXITCODE"
```

Expected output ends with `LIST_EXIT=0`, with the same agent ID and no second enrollment. Result: **PENDING MANUAL ACCEPTANCE**.

## 9. Packet capture: no plaintext heartbeat or identity

Capture locally on the controller while the agent connects and exchanges at least two heartbeats:

```powershell
$CaptureEtl = Join-Path $env:TEMP 'phantomlink-phase2.etl'
$CapturePcap = Join-Path $env:TEMP 'phantomlink-phase2.pcapng'
pktmon filter remove | Out-Null
pktmon filter add -p 5443 | Out-Null
pktmon filter add -p 5444 | Out-Null
pktmon start --capture --pkt-size 0 --file-name $CaptureEtl
# Wait for enrollment/connection and at least two heartbeats.
pktmon stop
pktmon etl2pcap $CaptureEtl --out $CapturePcap
if (-not (Test-Path -LiteralPath $CapturePcap -PathType Leaf)) { throw 'capture conversion failed' }
$DeviceJson = & $Python -m C2.managed_auth list-devices --db $env:PHANTOMLINK_MANAGED_DB
if ($LASTEXITCODE -ne 0) { throw 'list-devices failed before capture scan' }
$Devices = @($DeviceJson | ConvertFrom-Json)
if ($Devices.Count -ne 1) { throw "expected exactly one enrolled device; found $($Devices.Count)" }
$AgentId = [string]$Devices[0].agent_id
if ($AgentId -notmatch '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$') {
    throw 'enrolled agent ID absent or invalid'
}
$CaptureText = [Text.Encoding]::UTF8.GetString([IO.File]::ReadAllBytes($CapturePcap))
$Leaks = @('PING','PONG',$AgentId) | Where-Object { $CaptureText.Contains($_) }
if ($Leaks) { throw "plaintext managed content found: $($Leaks -join ',')" }
'PLAINTEXT_SCAN=PASS'
```

Expected output ends with `PLAINTEXT_SCAN=PASS`. Inspect the PCAPNG in the approved analyzer too: TLS records may be visible, but heartbeat and identity content must not be plaintext. Result: **PENDING MANUAL ACCEPTANCE**.

## 10. Negative listener proof

```powershell
$ControllerVpnIp = '10.8.0.1'
$ManagedPorts = @(5443,5444)
$Unexpected = Get-NetTCPConnection -State Listen |
    Where-Object LocalPort -in $ManagedPorts |
    Where-Object LocalAddress -ne $ControllerVpnIp
if ($Unexpected) { $Unexpected | Format-Table -AutoSize; throw 'unexpected managed listener' }
foreach ($Forbidden in @('0.0.0.0','::')) {
    if (Get-NetTCPConnection -State Listen -LocalAddress $Forbidden -ErrorAction SilentlyContinue |
        Where-Object LocalPort -in $ManagedPorts) { throw "wildcard listener: $Forbidden" }
}
'NEGATIVE_LISTENER_PROOF=PASS'
```

Expected output: `NEGATIVE_LISTENER_PROOF=PASS`. Also inspect every active LAN/public adapter and verify neither managed port is bound there. Result: **PENDING MANUAL ACCEPTANCE**.

## 11. Backup, integrity, recovery, and logs

Stop the controller before making a coherent backup:

```powershell
$BackupRoot = Join-Path 'C:\ProgramData\PhantomLink\backups' (Get-Date -Format 'yyyyMMdd-HHmmss')
New-Item -ItemType Directory -Force $BackupRoot | Out-Null
@($env:PHANTOMLINK_MANAGED_DB,$env:PHANTOMLINK_CA_CERT,$env:PHANTOMLINK_CA_KEY,$env:PHANTOMLINK_TLS_CERT,$env:PHANTOMLINK_TLS_KEY) |
    Where-Object { Test-Path -LiteralPath $_ } | Copy-Item -Destination $BackupRoot -Force
$Phase1Backup = Join-Path $env:PHANTOMLINK_MANAGED_STORE 'phase1-backup'
if (Test-Path -LiteralPath $Phase1Backup) { Copy-Item $Phase1Backup (Join-Path $BackupRoot 'phase1-backup') -Recurse }
Get-ChildItem $BackupRoot -File -Recurse | Get-FileHash -Algorithm SHA256 | Select-Object Path,Hash
& $Python -c "import sqlite3; c=sqlite3.connect(r'$env:PHANTOMLINK_MANAGED_DB'); print(c.execute('PRAGMA integrity_check').fetchone()[0]); c.close()"
```

Expected integrity output: `ok`.

Recover a chosen Phase 2 SQLite backup with this local controller block. It stops the one matching controller process, copies the current database to a timestamped side file, restores the explicitly selected Phase 2 database backup, reapplies private ACLs, runs integrity checking with the project Python, and restarts the controller. It never imports or copies Phase 1 `devices.bin` or `tokens.json` into SQLite.

```powershell
$ErrorActionPreference = 'Stop'
Set-Location 'G:\for_hack_all\Link_all - Copy'
$Python = (Resolve-Path '.\.venv\Scripts\python.exe').Path
$SelectedDatabaseBackup = 'C:\ProgramData\PhantomLink\backups\20260813-120000\managed.db'
if (-not (Test-Path -LiteralPath $SelectedDatabaseBackup -PathType Leaf)) { throw 'chosen Phase 2 database backup is missing' }
$SelectedDatabaseHash = (Get-FileHash -LiteralPath $SelectedDatabaseBackup -Algorithm SHA256 -ErrorAction Stop).Hash
$ControllerProcesses = @(Get-CimInstance Win32_Process | Where-Object {
    $_.CommandLine -match '(?i)-m\s+C2\.C2' -and $_.ProcessId -ne $PID
})
if ($ControllerProcesses.Count -gt 1) { throw 'ambiguous controller process set' }
if ($ControllerProcesses.Count -eq 1) {
    Stop-Process -Id $ControllerProcesses[0].ProcessId -Force -ErrorAction Stop
    Wait-Process -Id $ControllerProcesses[0].ProcessId -Timeout 10 -ErrorAction SilentlyContinue
}
'CONTROLLER_STOPPED=PASS'
$RecoveryStamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$CurrentAside = "$env:PHANTOMLINK_MANAGED_DB.pre-recovery-$RecoveryStamp"
if (Test-Path -LiteralPath $env:PHANTOMLINK_MANAGED_DB) {
    Copy-Item -LiteralPath $env:PHANTOMLINK_MANAGED_DB -Destination $CurrentAside -Force -ErrorAction Stop
}
Copy-Item -LiteralPath $SelectedDatabaseBackup -Destination $env:PHANTOMLINK_MANAGED_DB -Force -ErrorAction Stop
$RestoredDatabaseHash = (Get-FileHash -LiteralPath $env:PHANTOMLINK_MANAGED_DB -Algorithm SHA256 -ErrorAction Stop).Hash
if ($RestoredDatabaseHash -ne $SelectedDatabaseHash) { throw 'restored database hash mismatch' }
foreach ($PrivatePath in @($env:PHANTOMLINK_MANAGED_DB,$env:PHANTOMLINK_CA_KEY,$env:PHANTOMLINK_TLS_KEY)) {
    if (-not (Test-Path -LiteralPath $PrivatePath -PathType Leaf)) { throw "private path missing: $PrivatePath" }
    icacls $PrivatePath /inheritance:r | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "icacls failed to remove inheritance: $PrivatePath" }
    icacls $PrivatePath /grant:r "${env:USERDOMAIN}\${env:USERNAME}:(F)" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "icacls failed to grant current user: $PrivatePath" }
}
$Integrity = & $Python -c "import sqlite3; c=sqlite3.connect(r'$env:PHANTOMLINK_MANAGED_DB'); print(c.execute('PRAGMA integrity_check').fetchone()[0]); c.close()"
if ($LASTEXITCODE -ne 0 -or $Integrity -ne 'ok') { throw "database integrity failed: $Integrity" }
'DATABASE_INTEGRITY=ok'
$RecoveryOut = Join-Path $env:TEMP "phantomlink-recovery-$RecoveryStamp.out.log"
$RecoveryErr = Join-Path $env:TEMP "phantomlink-recovery-$RecoveryStamp.err.log"
$ControllerProcess = Start-Process -FilePath $Python -ArgumentList @('-u','-m','C2.C2') -WorkingDirectory (Get-Location).Path -WindowStyle Hidden -RedirectStandardOutput $RecoveryOut -RedirectStandardError $RecoveryErr -PassThru
$Deadline = (Get-Date).AddSeconds(15)
do {
    Start-Sleep -Milliseconds 250
    $Started = Test-Path $RecoveryOut -PathType Leaf -and (Select-String -LiteralPath $RecoveryOut -SimpleMatch '[+] Managed TLS on' -Quiet)
} until ($Started -or $ControllerProcess.HasExited -or (Get-Date) -ge $Deadline)
if (-not $Started) { Get-Content $RecoveryErr -ErrorAction SilentlyContinue; throw 'controller recovery restart failed' }
'RECOVERY_RESTART=PASS'
```

Expected literal output includes:

```text
CONTROLLER_STOPPED=PASS
DATABASE_INTEGRITY=ok
RECOVERY_RESTART=PASS
```

Then repeat Sections 3, 8, and 10.

```powershell
Get-Content -LiteralPath 'C:\ProgramData\PhantomLink\managed-agent.log' -Tail 100
& $Python -m C2.managed_auth list-audit --db $env:PHANTOMLINK_MANAGED_DB --limit 100
```

Expected: recent structured log events and one compact audit JSON array with exit code `0`; no tokens, private keys, DPAPI blobs, or certificate PEM.

Known ceilings: VPN availability is external; renewal timing, two-machine behavior, dashboard keystrokes, and packet inspection require manual acceptance. SQLite uses short-lived connections and has no persistent repository handle to close.

## 12. Roll back to preserved Phase 1 stores

With controller and agent stopped:

```powershell
$RollbackArchive = Join-Path $env:PHANTOMLINK_MANAGED_STORE ('phase2-rollback-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
New-Item -ItemType Directory -Force $RollbackArchive | Out-Null
foreach ($Path in @($env:PHANTOMLINK_MANAGED_DB,"$env:PHANTOMLINK_MANAGED_DB-wal","$env:PHANTOMLINK_MANAGED_DB-shm",$env:PHANTOMLINK_CA_CERT,$env:PHANTOMLINK_CA_KEY,$env:PHANTOMLINK_TLS_CERT,$env:PHANTOMLINK_TLS_KEY)) {
    if (Test-Path -LiteralPath $Path) { Move-Item -LiteralPath $Path -Destination $RollbackArchive }
}
$Phase1Backup = Join-Path $env:PHANTOMLINK_MANAGED_STORE 'phase1-backup'
foreach ($Name in @('devices.bin','tokens.json')) {
    $Source = Join-Path $Phase1Backup $Name
    if (Test-Path -LiteralPath $Source) { Copy-Item -LiteralPath $Source -Destination (Join-Path $env:PHANTOMLINK_MANAGED_STORE $Name) -Force }
}
$env:PHANTOMLINK_MANAGED_HOST = $null
$env:PHANTOMLINK_MANAGED_DB = $null
$env:PHANTOMLINK_CA_CERT = $null
$env:PHANTOMLINK_CA_KEY = $null
$env:PHANTOMLINK_TLS_CERT = $null
$env:PHANTOMLINK_TLS_KEY = $null
& $Python -c "import config; print('PHASE2_CONFIG=' + ('READY' if config.managed_phase2_enabled() else 'DISABLED'))"
```

Expected output: `PHASE2_CONFIG=DISABLED`. The archive keeps Phase 2 reversible; verified `phase1-backup` copies restore legacy bytes without decrypting or rewriting them.
