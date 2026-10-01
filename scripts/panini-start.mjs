import { spawn } from 'node:child_process';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const require = createRequire(import.meta.url);
let electron;
try {
  electron = require('electron');
} catch (error) {
  if (error?.code !== 'MODULE_NOT_FOUND') throw error;
  console.error(
    'PANINI Electron is missing. Close PANINI and run setup-panini.bat from the repository root.',
  );
  process.exit(1);
}
const environment = { ...process.env, PANINI_NODE: process.execPath };
if (process.argv.includes('--code')) {
  const workspaceIndex = process.argv.indexOf('--workspace');
  if (workspaceIndex >= 0 && !process.argv[workspaceIndex + 1])
    throw new Error('--workspace needs a directory');
  environment.PANINI_CLI_ENABLED = 'true';
  environment.PANINI_CLI_WORKSPACES = JSON.stringify([
    workspaceIndex >= 0 ? resolve(process.argv[workspaceIndex + 1]) : root,
  ]);
  environment.PANINI_CLI_MODEL ||=
    environment.JEV_REV_CODE_MODEL ||
    environment.PANINI_AGENT_MODEL ||
    environment.PANINI_PLANNER_MODEL ||
    environment.EXTENSION_PLANNER_MODEL ||
    'gpt-4.1';
  environment.PANINI_CLI_REASONING_EFFORT ||= 'low';
}
// Electron treats the presence of this variable as Node mode, even when empty.
delete environment.ELECTRON_RUN_AS_NODE;
const child = spawn(electron, [resolve(root, 'panini-core/apps/shell')], {
  cwd: root,
  windowsHide: true,
  stdio: 'inherit',
  env: environment,
});
child.on('error', (error) => {
  console.error(`PANINI could not start Electron: ${error.message}`);
  process.exitCode = 1;
});
child.on('exit', (code) => {
  process.exitCode = code ?? 1;
});
