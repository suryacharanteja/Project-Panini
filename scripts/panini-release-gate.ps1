param(
    [string]$PackageRoot,
    [string]$RuntimeRoot,
    [switch]$BuildOnly,
    [string]$QualificationEvidence = $env:PANINI_RELEASE_QUALIFICATION_EVIDENCE
)

$ErrorActionPreference = 'Stop'
function Get-PaniniSha256([string]$Path) {
    $stream = [IO.File]::OpenRead($Path)
    $sha = [Security.Cryptography.SHA256]::Create()
    try { return [BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-', '') }
    finally { $stream.Dispose(); $sha.Dispose() }
}
$workspace = Split-Path -Path $PSScriptRoot -Parent
if (!$PackageRoot) { $PackageRoot = Join-Path $workspace 'artifacts/panini-package' }
if (!$RuntimeRoot) { $RuntimeRoot = Join-Path $PackageRoot 'win-unpacked/resources/runtime' }
$installer = Join-Path $PackageRoot 'PANINI Setup 0.1.0.exe'
$unpacked = Join-Path $PackageRoot 'win-unpacked'
$manifestPath = Join-Path $RuntimeRoot 'release.json'
if (!(Test-Path -LiteralPath $installer) -or !(Test-Path -LiteralPath $manifestPath)) {
    throw 'Release artifacts are missing.'
}

$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
if ($manifest.product -ne 'PANINI') { throw 'The runtime manifest has the wrong product.' }
if ($manifest.files.Count -lt 1) { throw 'The runtime manifest has no files.' }
$listed = @{}
foreach ($entry in $manifest.files) {
    if ($entry.path -match '(^|/)\.env($|\.)') { throw 'A secret-bearing .env is bundled.' }
    $relative = $entry.path.Replace('/', '\')
    if ([IO.Path]::IsPathRooted($relative) -or (($entry.path -split '/') -contains '..')) {
        throw "Unsafe manifest path: $relative"
    }
    if ($listed.ContainsKey($entry.path)) { throw "Duplicate manifest path: $relative" }
    $path = Join-Path $RuntimeRoot $relative
    if (!(Test-Path -LiteralPath $path -PathType Leaf)) { throw "Missing runtime file: $relative" }
    if ((Get-Item -LiteralPath $path).Length -ne $entry.bytes) { throw "Size mismatch: $relative" }
    if ((Get-PaniniSha256 $path) -ne $entry.sha256) {
        throw "Hash mismatch: $relative"
    }
    $listed[$entry.path] = $true
}
$runtimePrefix = (Resolve-Path -LiteralPath $RuntimeRoot).Path.TrimEnd('\') + '\'
foreach ($item in Get-ChildItem -LiteralPath $RuntimeRoot -Recurse -Force) {
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "A runtime reparse point is not allowed: $($item.FullName)"
    }
    if ($item.PSIsContainer) { continue }
    $relative = $item.FullName.Substring($runtimePrefix.Length).Replace('\', '/')
    if ($relative -ne 'release.json' -and !$listed.ContainsKey($relative)) {
        throw "Unlisted runtime file: $relative"
    }
}
foreach ($required in @('cli/PANINI-CLI.exe', 'backend/Panini.Backend.exe', 'desktop/host/Panini.RuntimeHost.exe')) {
    if (!$listed.ContainsKey($required)) { throw "Required runtime file missing: $required" }
}
Write-Output "Verified $($manifest.files.Count) packaged runtime file hashes."
if ($manifest.signatureStatus -eq 'unsigned-development-candidate') {
    throw 'This artifact is explicitly marked as an unsigned development candidate.'
}
if (!$BuildOnly) {
    if (!$QualificationEvidence -or !(Test-Path -LiteralPath $QualificationEvidence -PathType Leaf)) {
        throw 'Release promotion requires PANINI_RELEASE_QUALIFICATION_EVIDENCE from a clean VM.'
    }
    $qualification = Get-Content -LiteralPath $QualificationEvidence -Raw | ConvertFrom-Json
    if ($qualification.product -ne 'PANINI' -or $qualification.status -ne 'passed' -or
        $qualification.kind -ne 'clean-vm-functional-qualification') {
        throw 'Clean-VM qualification evidence is not a passing PANINI run.'
    }
    if ($qualification.installerSha256 -ne (Get-PaniniSha256 $installer)) {
        throw 'Clean-VM evidence does not match this exact installer.'
    }
    if ($qualification.windowsBuild -lt 22000 -or
        $qualification.installerSignature -ne 'Valid' -or
        !$qualification.runId -or !$qualification.startedAt -or !$qualification.finishedAt) {
        throw 'Clean-VM evidence lacks a signed Windows 11 qualification run.'
    }
    foreach ($check in @('noExternalSandboxDependency', 'installAndShortcut',
        'installedRuntimeIntegrity',
        'cliLaunchAndKeyOnboarding', 'credentialRoundTrip', 'repairInstall',
        'installedNativeSandbox', 'installedHardLinkBoundary',
        'installedSystem32DllBoundary', 'uninstall')) {
        if ($qualification.checks.$check -ne 'passed') {
            throw "Clean-VM qualification is missing: $check"
        }
    }
}
$expectedPublisher = $env:PANINI_RELEASE_PUBLISHER_THUMBPRINT
if ($expectedPublisher -notmatch '^[A-Fa-f0-9]{40}$') {
    throw 'PANINI_RELEASE_PUBLISHER_THUMBPRINT must name the approved code-signing certificate.'
}

$executables = @($installer, (Join-Path $unpacked 'PANINI.exe'))
$executables += @($manifest.files | Where-Object { $_.path.EndsWith('.exe', [StringComparison]::OrdinalIgnoreCase) } |
    ForEach-Object { Join-Path $RuntimeRoot $_.path.Replace('/', '\') })
$ownedExecutables = @($installer, (Join-Path $unpacked 'PANINI.exe'),
    (Join-Path $RuntimeRoot 'cli/PANINI-CLI.exe'),
    (Join-Path $RuntimeRoot 'backend/Panini.Backend.exe'),
    (Join-Path $RuntimeRoot 'desktop/host/Panini.RuntimeHost.exe'),
    (Join-Path $RuntimeRoot 'desktop/broker/Panini.Desktop.Broker.exe'),
    (Join-Path $RuntimeRoot 'desktop/worker/Panini.Desktop.Worker.exe'),
    (Join-Path $RuntimeRoot 'desktop/assistant/PANINI Desktop Readiness.exe'))
$publisher = $null
foreach ($path in $executables) {
    if (!(Test-Path -LiteralPath $path -PathType Leaf)) { throw "Missing executable: $path" }
    $signature = Get-AuthenticodeSignature -LiteralPath $path
    if ($signature.Status -ne 'Valid') { throw "Invalid signature ($($signature.Status)): $path" }
    if ($ownedExecutables -contains $path) {
        $thumbprint = $signature.SignerCertificate.Thumbprint
        if ($thumbprint -ne $expectedPublisher) { throw "Unexpected publisher certificate: $path" }
        if (!$signature.TimeStamperCertificate) { throw "Missing trusted timestamp: $path" }
        if (!$publisher) { $publisher = $thumbprint }
        elseif ($publisher -ne $thumbprint) { throw "Publisher mismatch: $path" }
    }
}

if ($BuildOnly) {
    Write-Output "PANINI signed candidate gate passed: $($manifest.files.Count) runtime hashes and $($executables.Count) executable signatures."
} else {
    Write-Output "PANINI release promotion gate passed: hashes, signatures, and clean-VM qualification."
}
