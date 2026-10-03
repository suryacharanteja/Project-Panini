# PANINI sandbox computers (V2-11): build | up | down | status | setup.
# A thin wrapper: tokens come from PANINI's DPAPI secret store through scripts/panini-sandbox.py.
param([ValidateSet('setup', 'build', 'up', 'down', 'status')][string]$Command = 'status')
$root = Split-Path -Parent $PSScriptRoot
& (Join-Path $root '.venv\Scripts\python.exe') (Join-Path $root 'scripts\panini-sandbox.py') $Command
exit $LASTEXITCODE
