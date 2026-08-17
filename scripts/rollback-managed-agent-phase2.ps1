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

$ImplementationPaths = @(
    '.',
    ':(exclude).gitattributes',
    ':(exclude)debug-artifacts/managed-agent-phase2.patch',
    ':(exclude)debug-artifacts/managed-agent-phase2-verification.md',
    ':(exclude)scripts/rollback-managed-agent-phase2.ps1',
    ':(exclude)debug-artifacts/managed-agent-phase2-preflight/**'
)
$EvidencePaths = @(
    '.gitattributes',
    'debug-artifacts/managed-agent-phase2.patch',
    'debug-artifacts/managed-agent-phase2-verification.md',
    'scripts/rollback-managed-agent-phase2.ps1',
    'debug-artifacts/managed-agent-phase2-preflight'
)
$ExpectedPatchHash = '4C3CC1C33BA74805B64249347B5346ACD16CCD8430776CB828F3570CD5DC5EAC'
$BaseCommit = 'af64499'
$OriginalHead = (& git -C $RepoRoot rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0) { throw 'could not resolve starting HEAD' }

& git -C $RepoRoot diff --quiet
if ($LASTEXITCODE -ne 0) { throw 'tracked worktree must be clean before rollback' }
& git -C $RepoRoot diff --cached --quiet
if ($LASTEXITCODE -ne 0) { throw 'tracked index must be clean before rollback' }

$StatusBefore = (& git -C $RepoRoot status --short) -join "`n"
$PatchHash = (Get-FileHash -LiteralPath $PatchPath -Algorithm SHA256).Hash
if ($PatchHash -ne $ExpectedPatchHash) {
    throw "patch hash mismatch: expected $ExpectedPatchHash, got $PatchHash"
}
"REPO_ROOT=$RepoRoot"
"PATCH_PATH=$PatchPath"
"PATCH_SHA256=$PatchHash"
"ORIGINAL_HEAD=$OriginalHead"
"STATUS_BEFORE_BEGIN"
$StatusBefore
"STATUS_BEFORE_END"

$Applied = $false
try {
    & git -C $RepoRoot apply --reverse --index --check $PatchPath
    if ($LASTEXITCODE -ne 0) { throw 'reverse patch index check failed' }
    'REVERSE_INDEX_CHECK=PASS'
    & git -C $RepoRoot apply --reverse --index $PatchPath
    if ($LASTEXITCODE -ne 0) { throw 'reverse patch index apply failed' }
    $Applied = $true
    'REVERSE_INDEX_APPLY=PASS'

    & git -C $RepoRoot diff --check -- $ImplementationPaths
    if ($LASTEXITCODE -ne 0) { throw 'rollback implementation diff check failed' }
    & git -C $RepoRoot diff --quiet -- $ImplementationPaths
    if ($LASTEXITCODE -ne 0) { throw 'rollback worktree does not match its index' }
    & git -C $RepoRoot diff --cached --quiet $BaseCommit -- $ImplementationPaths
    if ($LASTEXITCODE -ne 0) { throw "rollback index does not reproduce base $BaseCommit" }
    & git -C $RepoRoot diff --quiet $OriginalHead -- $EvidencePaths
    if ($LASTEXITCODE -ne 0) { throw 'rollback changed an evidence role' }
} catch {
    $Failure = $_
    if ($Applied) {
        & git -C $RepoRoot restore --source $OriginalHead --staged --worktree -- $ImplementationPaths
        if ($LASTEXITCODE -ne 0) {
            throw "rollback verification failed and tracked recovery failed: $Failure"
        }
        'ROLLBACK_RECOVERY=PASS'
    }
    throw $Failure
}
'ROLLBACK_BASE=af64499'
'ROLLBACK_INDEX_VERIFY=PASS'
'ROLLBACK_WORKTREE_VERIFY=PASS'
'EVIDENCE_ROLES_PRESERVED=PASS'

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
