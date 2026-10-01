import { spawn } from 'node:child_process';
import { build } from 'esbuild';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
process.chdir(root);
async function run(command, args) {
  await new Promise((resolve, reject) => {
    const child = spawn(command, args, {
      cwd: root,
      windowsHide: true,
      stdio: 'inherit',
      env: { ...process.env, DOTNET_CLI_TELEMETRY_OPTOUT: '1' },
    });
    child.on('error', reject);
    child.on('exit', (code) =>
      code === 0 ? resolve() : reject(new Error(`${command} exited ${code}`)),
    );
  });
}
await build({
  entryPoints: ['panini-core/capabilities/search/src/worker.ts'],
  bundle: true,
  platform: 'node',
  format: 'esm',
  outfile: 'panini-core/capabilities/search/dist-worker/worker.mjs',
  packages: 'bundle',
  banner: {
    js: 'import { createRequire as paniniCreateRequire } from "node:module"; const require = paniniCreateRequire(import.meta.url);',
  },
});
await run('dotnet', [
  'build',
  'panini-core/capabilities/desktop/Panini.Desktop.sln',
  '-c',
  'Release',
]);
await run(process.execPath, ['panini-core/apps/shell/scripts/build.mjs']);
// PANINI Admin builds into frontend/public/admin, which packaging copies with the console.
await run(process.execPath, [
  process.env.npm_execpath,
  'run',
  'build',
  '--workspace',
  'panini-core/apps/admin',
]);
