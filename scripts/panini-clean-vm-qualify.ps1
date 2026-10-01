<#
Run only on a disposable, otherwise clean Windows 11 VM. This is functional
qualification; an unsigned candidate can never satisfy the release gate.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Installer,
    [switch]$DisposableMachine,
    [switch]$AllowUnsignedCandidate,
    [string]$EvidencePath
)

$ErrorActionPreference = 'Stop'
function Invoke-BoundedInstaller([string]$Path, [string[]]$Arguments, [string]$Label) {
    $process = Start-Process -FilePath $Path -ArgumentList $Arguments -WindowStyle Hidden -PassThru
    try {
        if (!$process.WaitForExit(300000)) {
            $process.Kill($true)
            $process.WaitForExit()
            throw "$Label exceeded the five-minute limit."
        }
        if ($process.ExitCode -ne 0) { throw "$Label exit code: $($process.ExitCode)" }
    }
    finally { $process.Dispose() }
}
if (!$DisposableMachine) { throw 'Pass -DisposableMachine only on a disposable clean Windows VM.' }
if ([Environment]::OSVersion.Version.Build -lt 22000) { throw 'This qualification requires Windows 11.' }
$installerPath = (Resolve-Path -LiteralPath $Installer).Path
if (!(Test-Path -LiteralPath $installerPath -PathType Leaf)) { throw 'Installer not found.' }
$signature = Get-AuthenticodeSignature -LiteralPath $installerPath
if (!$AllowUnsignedCandidate -and $signature.Status -ne 'Valid') {
    throw "Installer signature is not valid: $($signature.Status)"
}

$programs = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'
$desktopShortcut = Join-Path $programs 'PANINI.lnk'
$cliShortcut = Join-Path $programs 'PANINI-CLI.lnk'
$existing = @(Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*',
    'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*' -ErrorAction SilentlyContinue |
    Where-Object { $_.DisplayName -like 'PANINI*' })
if ($existing.Count -or (Test-Path -LiteralPath $desktopShortcut) -or
    (Test-Path -LiteralPath $cliShortcut)) {
    throw 'PANINI is already installed or shortcut-linked. Refusing to disturb existing state.'
}

$runId = [Guid]::NewGuid().ToString('N')
$runRoot = Join-Path $env:TEMP "panini-qualification-$runId"
$installDir = Join-Path $runRoot 'installation'
$workspace = Join-Path $runRoot 'workspace'
$resolvedTemp = (Resolve-Path -LiteralPath $env:TEMP).Path.TrimEnd('\') + '\'
if (!$runRoot.StartsWith($resolvedTemp, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Qualification path escaped the temporary directory.'
}
if ($runRoot.Contains(' ')) { throw 'NSIS /D target must have no spaces for this qualification.' }
if (!$EvidencePath) { $EvidencePath = Join-Path $runRoot 'evidence.json' }

$evidence = [ordered]@{
    product = 'PANINI'
    kind = 'clean-vm-functional-qualification'
    runId = $runId
    startedAt = [DateTime]::UtcNow.ToString('o')
    windowsBuild = [Environment]::OSVersion.Version.Build
    installerSignature = [string]$signature.Status
    installerSha256 = (Get-FileHash -LiteralPath $installerPath -Algorithm SHA256).Hash
    installer = $installerPath
    installDir = $installDir
    checks = [ordered]@{}
    status = 'running'
}
New-Item -ItemType Directory -Path $workspace -Force | Out-Null
try {
    foreach ($command in @('docker.exe', 'codex.exe')) {
        if (Get-Command $command -ErrorAction SilentlyContinue) {
            throw "$command is installed; this is not a dependency-free VM."
        }
    }
    $evidence.checks.noExternalSandboxDependency = 'passed'

    Invoke-BoundedInstaller $installerPath @('/S', "/D=$installDir") 'Installer'
    $cli = Join-Path $installDir 'resources\runtime\cli\PANINI-CLI.exe'
    if (!(Test-Path -LiteralPath $cli -PathType Leaf)) { throw 'Installed PANINI-CLI.exe is missing.' }
    if (!(Test-Path -LiteralPath $cliShortcut -PathType Leaf)) { throw 'PANINI-CLI shortcut is missing.' }
    $shell = New-Object -ComObject WScript.Shell
    $link = $shell.CreateShortcut($cliShortcut)
    if ($link.TargetPath -ne $cli) { throw 'PANINI-CLI shortcut targets the wrong executable.' }
    $evidence.checks.installAndShortcut = 'passed'
    $runtimeRoot = Join-Path $installDir 'resources\runtime'
    & (Join-Path $PSScriptRoot 'panini-installed-runtime-integrity.ps1') -RuntimeRoot $runtimeRoot | Out-Null
    $evidence.checks.installedRuntimeIntegrity = 'passed'

    $env:OPENAI_API_KEY = ''
    $env:PANINI_RUNTIME_ROOT = ''
    $env:PANINI_DATA_DIR = Join-Path $runRoot 'data'
    $version = & $cli --version 2>&1 | Out-String
    if ($LASTEXITCODE -ne 0 -or $version -notmatch 'panini-cli') { throw 'Installed CLI failed to launch.' }
    $keyStatus = & $cli auth status 2>&1 | Out-String
    if ($LASTEXITCODE -ne 1 -or $keyStatus -notmatch 'missing') {
        throw 'A clean install did not report a missing API key.'
    }
    $evidence.checks.cliLaunchAndKeyOnboarding = 'passed'

    try {
        'sk-panini-qualification-only' | & $cli auth login --key-stdin | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Installed CLI could not save a test key.' }
        $keyStatus = & $cli auth status 2>&1 | Out-String
        if ($LASTEXITCODE -ne 0 -or $keyStatus -notmatch 'configured') {
            throw 'Installed CLI could not read the saved test key.'
        }
    }
    finally {
        & $cli auth logout | Out-Null
    }
    $keyStatus = & $cli auth status 2>&1 | Out-String
    if ($LASTEXITCODE -ne 1 -or $keyStatus -notmatch 'missing') {
        throw 'Installed CLI did not remove the test key.'
    }
    $evidence.checks.credentialRoundTrip = 'passed'

    Invoke-BoundedInstaller $installerPath @('/S', "/D=$installDir") 'Repair installer'
    if (!(Test-Path -LiteralPath $cli -PathType Leaf) -or
        !(Test-Path -LiteralPath $cliShortcut -PathType Leaf)) {
        throw 'Repair/reinstall failed or lost PANINI-CLI.'
    }
    & (Join-Path $PSScriptRoot 'panini-installed-runtime-integrity.ps1') -RuntimeRoot $runtimeRoot | Out-Null
    $evidence.checks.repairInstall = 'passed'

    $model = Join-Path $runRoot 'model.json'
    @(
        @{ calls = @(@{ name = 'run_command'; arguments = @{
            command = 'Write-Output PANINI_INSTALL_SMOKE'; timeout_seconds = 15
        } }) },
        @{ text = 'PANINI installation smoke completed.' }
    ) | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $model -Encoding utf8
    $env:PANINI_CLI_SCRIPTED_MODEL = $model
    $result = & $cli -C $workspace --sandbox native --approve-all exec 'Run installation smoke' 2>&1 | Out-String
    if ($LASTEXITCODE -ne 0 -or $result -notmatch 'PANINI_INSTALL_SMOKE') {
        throw "Installed native sandbox smoke failed: $result"
    }
    $evidence.checks.installedNativeSandbox = 'passed'

    $outside = Join-Path $runRoot 'private.txt'
    Set-Content -LiteralPath $outside -Value 'PANINI_PRIVATE_SENTINEL'
    $alias = Join-Path $workspace 'alias.txt'
    New-Item -ItemType HardLink -Path $alias -Target $outside | Out-Null
    $result = & $cli -C $workspace --sandbox native --approve-all exec 'Run hard-link boundary smoke' 2>&1 | Out-String
    if ($result -notmatch 'workspace hard link' -or
        (Get-Content -LiteralPath $outside -Raw).Trim() -ne 'PANINI_PRIVATE_SENTINEL') {
        throw 'Installed CLI did not reject the hard-link escape.'
    }
    Remove-Item -LiteralPath $alias
    $evidence.checks.installedHardLinkBoundary = 'passed'

    [IO.File]::WriteAllBytes((Join-Path $workspace 'processmodel.dll'), [byte[]](1, 2, 3, 4))
    $result = & $cli -C $workspace --sandbox native --approve-all exec 'Run DLL collision smoke' 2>&1 | Out-String
    if ($LASTEXITCODE -ne 0 -or $result -notmatch 'PANINI_INSTALL_SMOKE') {
        throw 'Installed CLI loaded or was blocked by a workspace DLL.'
    }
    $evidence.checks.installedSystem32DllBoundary = 'passed'

    $uninstallers = @(Get-ChildItem -LiteralPath $installDir -Filter '*uninstall*.exe' -File)
    if ($uninstallers.Count -ne 1) { throw 'Expected exactly one PANINI uninstaller.' }
    Invoke-BoundedInstaller $uninstallers[0].FullName @('/S') 'Uninstaller'
    if ((Test-Path -LiteralPath $cliShortcut) -or (Test-Path -LiteralPath $cli)) {
        throw 'Uninstall left the CLI or its shortcut behind.'
    }
    $evidence.checks.uninstall = 'passed'
    $evidence.status = 'passed'
}
catch {
    $evidence.status = 'failed'
    $evidence.error = $_.Exception.Message
    throw
}
finally {
    $evidence.finishedAt = [DateTime]::UtcNow.ToString('o')
    $parent = Split-Path -Path $EvidencePath -Parent
    if (!(Test-Path -LiteralPath $parent)) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
    $evidence | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $EvidencePath -Encoding utf8
    Write-Output "Qualification evidence: $EvidencePath"
}

