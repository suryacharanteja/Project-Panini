import { spawnSync } from 'node:child_process';
import { delimiter, dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const python = join(
  root,
  '.venv',
  process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python',
);
const pythonSources = [
  'backend',
  'panini-core/voice/live-spoken-conversation/panini',
  'panini-core/capabilities/cli/src',
  'panini-core/capabilities/cli/tests',
  'panini-core/capabilities/cli/run.py',
  'panini-core/capabilities/desktop/tests',
  'scripts',
];
const checks = [
  [process.execPath, [process.env.npm_execpath, 'run', 'format:check']],
  [python, ['-m', 'ruff', 'check', ...pythonSources]],
  [python, ['-m', 'ruff', 'format', '--check', ...pythonSources]],
  [python, ['-m', 'pytest', '-q', 'backend/tests']],
  [
    python,
    ['-m', 'pytest', '-q', '-p', 'no:cacheprovider', 'panini-core/capabilities/cli/tests'],
    'panini-core/capabilities/cli',
  ],
  [process.execPath, ['--test', 'frontend/test/*.test.mjs']],
  [process.execPath, [process.env.npm_execpath, 'run', 'test:search']],
  [
    process.execPath,
    [process.env.npm_execpath, 'run', 'test', '--workspace', 'panini-core/apps/admin'],
  ],
  [
    process.execPath,
    [process.env.npm_execpath, 'run', 'test:unit', '--workspace', 'panini-core/apps/shell'],
  ],
  [process.execPath, [process.env.npm_execpath, 'run', 'typecheck']],
  [process.execPath, [process.env.npm_execpath, 'run', 'panini:typecheck']],
];
let failed = false;
for (const [command, args, cwd = '.'] of checks) {
  const directory = join(root, cwd);
  // pytest runs from its project so each suite uses its own configuration.
  const adjusted =
    cwd === '.'
      ? args
      : args.map((value) => (value.startsWith(cwd + '/') ? value.slice(cwd.length + 1) : value));
  const result = spawnSync(command, adjusted, {
    cwd: directory,
    stdio: 'inherit',
    windowsHide: true,
    env:
      command === python
        ? {
            ...process.env,
            PYTHONPATH: [
              join(root, 'panini-core/voice/live-spoken-conversation'),
              root,
              join(root, 'panini-core/capabilities/cli/src'),
              process.env.PYTHONPATH,
            ]
              .filter(Boolean)
              .join(delimiter),
          }
        : process.env,
  });
  if (result.error) console.error(result.error.message);
  if (result.error || result.status !== 0) failed = true;
}
process.exitCode = failed ? 1 : 0;
