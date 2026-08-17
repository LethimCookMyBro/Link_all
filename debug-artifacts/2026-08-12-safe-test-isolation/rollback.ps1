param(
    [string]$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
)

$files = @(
    "requirements.txt",
    "C2\dashboard.py",
    "tests\test_dashboard.py",
    "tests\test_combined_launcher.py",
    "tests\test_phantomlink_deep_coverage.py"
)

foreach ($file in $files) {
    $backup = Join-Path $PSScriptRoot ((Split-Path $file -Leaf) + ".original")
    $target = Join-Path $Root $file
    New-Item -ItemType Directory -Force -Path (Split-Path $target) | Out-Null
    Copy-Item -LiteralPath $backup -Destination $target -Force
}

$runbook = Join-Path $Root "docs\runbooks\test-isolation-dashboard-lifecycle.md"
if (Test-Path -LiteralPath $runbook) {
    Remove-Item -LiteralPath $runbook -Force
}

Write-Output "Rollback restored five files and removed the runbook under: $Root"
