param([Parameter(Mandatory = $true)][string]$RuntimeRoot)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath $RuntimeRoot).Path
$manifestPath = Join-Path $root 'release.json'
if (!(Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
    throw 'Installed runtime manifest is missing.'
}
$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
if ($manifest.product -ne 'PANINI' -or $manifest.files.Count -lt 1) {
    throw 'Installed runtime manifest is invalid.'
}
$listed = @{}
foreach ($entry in $manifest.files) {
    $relative = [string]$entry.path
    if (!$relative -or $relative.Contains('\') -or $relative.Contains(':') -or
        [IO.Path]::IsPathRooted($relative) -or
        (($relative -split '/') | Where-Object { $_ -in @('', '.', '..') })) {
        throw "Unsafe installed runtime path: $relative"
    }
    if ($listed.ContainsKey($relative)) { throw "Duplicate installed runtime path: $relative" }
    $path = Join-Path $root $relative.Replace('/', '\')
    if (!(Test-Path -LiteralPath $path -PathType Leaf)) { throw "Installed runtime file missing: $relative" }
    $item = Get-Item -LiteralPath $path -Force
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Installed runtime reparse point: $relative"
    }
    if ($item.Length -ne $entry.bytes) { throw "Installed runtime size mismatch: $relative" }
    if ((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -ne $entry.sha256) {
        throw "Installed runtime hash mismatch: $relative"
    }
    $listed[$relative] = $true
}
$prefix = $root.TrimEnd('\') + '\'
foreach ($item in Get-ChildItem -LiteralPath $root -Recurse -Force) {
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Installed runtime reparse point: $($item.FullName)"
    }
    if ($item.PSIsContainer) { continue }
    $relative = $item.FullName.Substring($prefix.Length).Replace('\', '/')
    if ($relative -ne 'release.json' -and !$listed.ContainsKey($relative)) {
        throw "Unlisted installed runtime file: $relative"
    }
}
foreach ($required in @('cli/PANINI-CLI.exe', 'backend/Panini.Backend.exe', 'desktop/host/Panini.RuntimeHost.exe')) {
    if (!$listed.ContainsKey($required)) { throw "Installed runtime missing required file: $required" }
}
Write-Output "Verified $($manifest.files.Count) installed runtime file hashes."
