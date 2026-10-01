import { spawnSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
if (process.platform === 'win32') {
  const command = [
    '$ErrorActionPreference = "Stop";',
    'Get-CimInstance Win32_Process |',
    'Where-Object { $_.Name -in @("electron.exe", "node.exe", "Panini.RuntimeHost.exe", "Panini.Desktop.Broker.exe") } |',
    'Select-Object ProcessId, Name, CommandLine | ConvertTo-Json -Compress',
  ].join(' ');
  const result = spawnSync('powershell.exe', ['-NoProfile', '-Command', command], {
    cwd: root,
    encoding: 'utf8',
    windowsHide: true,
  });
  if (result.error || result.status !== 0) {
    console.error('Cannot check for running PANINI processes. Close PANINI and retry setup.');
    process.exit(1);
  }
  const processes = result.stdout.trim() ? JSON.parse(result.stdout) : [];
  const active = (Array.isArray(processes) ? processes : [processes]).filter((process) => {
    const line = (process.CommandLine || '').toLowerCase();
    const repository = root.toLowerCase();
    return (
      line.includes(repository) &&
      (process.Name.toLowerCase() === 'electron.exe' ||
        process.Name.toLowerCase() === 'panini.runtimehost.exe' ||
        process.Name.toLowerCase() === 'panini.desktop.broker.exe' ||
        line.includes('frontend\\server\\main.mjs') ||
        line.includes('frontend/server/main.mjs') ||
        line.includes('panini-core\\capabilities\\search') ||
        line.includes('panini-core/capabilities/search'))
    );
  });
  if (active.length) {
    console.error('PANINI is still running. Quit PANINI and any npm run dev session before setup.');
    console.error(
      `Running PANINI process IDs: ${active.map((process) => process.ProcessId).join(', ')}`,
    );
    console.error('No dependencies were changed. After closing them, rerun setup-panini.bat.');
    process.exit(1);
  }
}
function run(command, args) {
  const result = spawnSync(command, args, { cwd: root, stdio: 'inherit', windowsHide: true });
  if (result.error || result.status !== 0) {
    console.error(result.error?.message || 'Setup failed');
    process.exit(result.status || 1);
  }
}
const python = join(
  root,
  '.venv',
  process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python',
);
if (!existsSync(python))
  run(process.env.PYTHON || (process.platform === 'win32' ? 'py' : 'python3'), [
    ...(process.platform === 'win32' && !process.env.PYTHON ? ['-3.12'] : []),
    '-m',
    'venv',
    '.venv',
  ]);
run(python, ['-m', 'pip', 'install', '--upgrade', 'pip==26.2.1']);
run(python, ['-m', 'pip', 'install', '-r', 'backend/requirements-dev.txt']);
run(process.execPath, [process.env.npm_execpath, 'ci', '--include=dev']);
console.log(
  'Dependencies installed. Run npm run panini:build, then npm run panini. Chromium is launched by browser-use on demand.',
);
