param(
    [string]$Executable = (Join-Path (Split-Path $PSScriptRoot -Parent) 'artifacts/panini-cli/PANINI-CLI.exe')
)

$ErrorActionPreference = 'Stop'
$workspace = Split-Path $PSScriptRoot -Parent
$fixture = Join-Path $workspace 'panini-core/capabilities/cli/tests/fixtures/native_packaged_smoke.json'
$hostFixture = Join-Path $workspace 'panini-core/capabilities/cli/tests/fixtures/host_packaged_smoke.json'
if (!(Test-Path -LiteralPath $Executable -PathType Leaf)) { throw "Missing PANINI-CLI executable: $Executable" }
if (!(Test-Path -LiteralPath $fixture -PathType Leaf)) { throw "Missing smoke fixture: $fixture" }
if (!(Test-Path -LiteralPath $hostFixture -PathType Leaf)) { throw "Missing smoke fixture: $hostFixture" }
$temporary = Join-Path ([IO.Path]::GetTempPath()) ('panini-cli-packaged-smoke-' + [guid]::NewGuid().ToString('N'))
$smokeWorkspace = Join-Path $temporary 'workspace'
$data = Join-Path $temporary 'data'
$oldScript = $env:PANINI_CLI_SCRIPTED_MODEL
$oldData = $env:PANINI_DATA_DIR
try {
    New-Item -ItemType Directory -Path $smokeWorkspace -Force | Out-Null
    $env:PANINI_CLI_SCRIPTED_MODEL = $fixture
    $env:PANINI_DATA_DIR = $data
    $output = & $Executable -C $smokeWorkspace --approve-all --sandbox native exec 'Run the packaged native smoke command.' 2>&1 | Out-String
    if ($LASTEXITCODE -ne 0 -or $output -notmatch 'PANINI_NATIVE_PACKAGED_SMOKE' -or
        $output -match 'stopped:|Sandbox unavailable:|could not start:') {
        throw "Packaged native command failed: $output"
    }
    $session = Get-ChildItem -LiteralPath (Join-Path $data 'cli-sessions') -Filter '*.json' -File |
        Select-Object -First 1
    if (!$session) { throw 'Packaged CLI did not save a session.' }
    $receiptOutput = & $Executable receipts $session.BaseName 2>&1 | Out-String
    if ($LASTEXITCODE -ne 0 -or $receiptOutput -notmatch 'Verified [1-9][0-9]* receipt events') {
        throw "Packaged receipt verification failed: $receiptOutput"
    }
    $env:PANINI_CLI_SCRIPTED_MODEL = $hostFixture
    $hostOutput = & $Executable -C $smokeWorkspace --approve-all --sandbox native exec 'Run the packaged host-access smoke command.' 2>&1 | Out-String
    if ($LASTEXITCODE -ne 0 -or $hostOutput -notmatch 'PANINI_HOST_PACKAGED_SMOKE' -or
        ($hostOutput -notmatch 'scope: host' -and $hostOutput -notmatch 'Windows host access') -or
        $hostOutput -match 'stopped:|could not start:') {
        throw "Packaged host-access command failed: $hostOutput"
    }
    $hostSession = Get-ChildItem -LiteralPath (Join-Path $data 'cli-sessions') -Filter '*.json' -File |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (!$hostSession -or $hostSession.BaseName -eq $session.BaseName) {
        throw 'Packaged host-access CLI did not save a separate session.'
    }
    $hostReceipt = Join-Path $data "cli-sessions/receipts/$($hostSession.BaseName).jsonl"
    if (!(Test-Path -LiteralPath $hostReceipt -PathType Leaf) -or
        (Get-Content -LiteralPath $hostReceipt -Raw) -notmatch '"sandbox":"host"') {
        throw 'Packaged host-access receipt is missing its execution scope.'
    }
    $hostReceiptOutput = & $Executable receipts $hostSession.BaseName 2>&1 | Out-String
    if ($LASTEXITCODE -ne 0 -or $hostReceiptOutput -notmatch 'Verified [1-9][0-9]* receipt events') {
        throw "Packaged host-access receipt verification failed: $hostReceiptOutput"
    }
    Write-Output 'PANINI-CLI packaged smoke passed: native and host commands, sessions, and receipt chains verified.'
} finally {
    $env:PANINI_CLI_SCRIPTED_MODEL = $oldScript
    $env:PANINI_DATA_DIR = $oldData
    $resolvedTemp = [IO.Path]::GetFullPath($temporary)
    $systemTemp = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\'
    if ($resolvedTemp.StartsWith($systemTemp, [StringComparison]::OrdinalIgnoreCase) -and
        $resolvedTemp -match 'panini-cli-packaged-smoke-[a-f0-9]{32}$' -and
        (Test-Path -LiteralPath $resolvedTemp)) {
        Remove-Item -LiteralPath $resolvedTemp -Recurse -Force
    }
}
