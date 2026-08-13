[CmdletBinding()]
param(
    [string]$RepoRoot = (Get-Location).Path,
    [string]$PatchPath = ''
)

$ErrorActionPreference = 'Stop'
$RepoRoot = [IO.Path]::GetFullPath((Resolve-Path -LiteralPath $RepoRoot).Path).TrimEnd('\', '/')
if (-not $PatchPath) {
    $PatchPath = Join-Path $RepoRoot 'debug-artifacts\managed-agent-phase2.patch'
}
$PatchPath = (Resolve-Path -LiteralPath $PatchPath).Path
$RootPrefix = $RepoRoot + [IO.Path]::DirectorySeparatorChar
if (-not $PatchPath.StartsWith($RootPrefix, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'patch must be below RepoRoot'
}

$GitRoot = [IO.Path]::GetFullPath(
    (& git -C $RepoRoot rev-parse --show-toplevel).Trim()
).TrimEnd('\', '/')
if ($LASTEXITCODE -ne 0 -or -not $GitRoot.Equals($RepoRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'RepoRoot must be the Git worktree root'
}

$StatusBefore = (& git -C $RepoRoot status --short) -join "`n"
$PatchHash = (Get-FileHash -LiteralPath $PatchPath -Algorithm SHA256).Hash
"REPO_ROOT=$RepoRoot"
"PATCH_PATH=$PatchPath"
"PATCH_SHA256=$PatchHash"
"STATUS_BEFORE_BEGIN"
$StatusBefore
"STATUS_BEFORE_END"

& git -C $RepoRoot apply --reverse --check $PatchPath
if ($LASTEXITCODE -ne 0) { throw 'reverse patch check failed' }
'REVERSE_CHECK=PASS'
& git -C $RepoRoot apply --reverse $PatchPath
if ($LASTEXITCODE -ne 0) { throw 'reverse patch apply failed' }
'REVERSE_APPLY=PASS'
& git -C $RepoRoot diff --check
if ($LASTEXITCODE -ne 0) { throw 'rollback diff check failed' }
& git -C $RepoRoot diff --quiet af64499 -- .
if ($LASTEXITCODE -ne 0) { throw 'rollback does not reproduce base af64499' }
'ROLLBACK_BASE=af64499'
'ROLLBACK_VERIFY=PASS'

$ManagedStore = if ($env:PHANTOMLINK_MANAGED_STORE) {
    $env:PHANTOMLINK_MANAGED_STORE
} else {
    Join-Path $RepoRoot 'managed-store'
}
$Database = if ($env:PHANTOMLINK_MANAGED_DB) {
    $env:PHANTOMLINK_MANAGED_DB
} else {
    Join-Path $ManagedStore 'managed.db'
}
"ARCHIVE_MANUALLY_DATABASE=$Database"
"ARCHIVE_MANUALLY_CA_CERT=$($env:PHANTOMLINK_CA_CERT)"
"ARCHIVE_MANUALLY_CA_KEY=$($env:PHANTOMLINK_CA_KEY)"
"ARCHIVE_MANUALLY_TLS_CERT=$($env:PHANTOMLINK_TLS_CERT)"
"ARCHIVE_MANUALLY_TLS_KEY=$($env:PHANTOMLINK_TLS_KEY)"
"ARCHIVE_MANUALLY_PHASE1_BACKUP=$(Join-Path $ManagedStore 'phase1-backup')"
