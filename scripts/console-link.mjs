/** Open the running PANINI runtime's web console with a fresh one-time sign-in link. */
import { spawn } from 'node:child_process';
import { mintTicket } from '../frontend/server/console-auth.mjs';
import { runtimeFile } from '../frontend/server/runtime-file.mjs';

const runtime = runtimeFile().read();
let running = false;
try {
  running = Boolean(runtime?.pid) && process.kill(runtime.pid, 0);
} catch {}
if (!running || !runtime.console_secret) {
  console.error('PANINI is not running. Start it with npm run panini or npm run dev.');
  process.exit(1);
}
const url = `http://localhost:${runtime.port}/auth/login?ticket=${mintTicket(runtime.console_secret)}`;
console.log(`Web console (one-time sign-in link, 10 minutes): ${url}`);
if (!process.argv.includes('--print')) {
  const [command, args] =
    process.platform === 'win32'
      ? ['explorer.exe', [url]]
      : process.platform === 'darwin'
        ? ['open', [url]]
        : ['xdg-open', [url]];
  spawn(command, args, { detached: true, stdio: 'ignore', windowsHide: true }).unref();
}
