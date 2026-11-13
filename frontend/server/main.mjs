/**
 * PANINI runtime: one supervised process tree for every surface.
 *
 *   npm run dev           development runtime (search via Vite); prints a console sign-in link
 *   npm start             the same with the production search build
 *   PANINI shell          launches this with --managed and supplies its own launch secrets
 *
 * The backend always runs the PANINI coordinator, so the shell (the avatar), the
 * web console and the paired extension share one set of capabilities at once.
 */
import { spawn } from 'node:child_process';
import { randomBytes, randomUUID } from 'node:crypto';
import { existsSync } from 'node:fs';
import { createRequire } from 'node:module';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createSigninGate } from './signin-gate.mjs';
import { createGateway } from './gateway.mjs';
import { readConfig } from './config.mjs';
import { snapshotRelease } from './release.mjs';
import { runtimeFile } from './runtime-file.mjs';

const root =
  process.env.PANINI_RUNTIME_ROOT || join(dirname(fileURLToPath(import.meta.url)), '..', '..');
if (existsSync(join(root, '.env'))) process.loadEnvFile(join(root, '.env'));
const production = process.argv.includes('--production');
const managed = process.argv.includes('--managed');
const { port, backendPort, searchPort, searchConfigured, allowedOrigins } = readConfig(
  process.env,
  production,
);
const backendUrl = `http://127.0.0.1:${backendPort}`;
const searchUrl = `http://127.0.0.1:${searchPort}`;
const token = () => randomBytes(32).toString('base64url');

// Per-launch identities. The shell passes its own so it can authenticate as controller.
const launch = {
  PANINI_INSTANCE_ID: process.env.PANINI_INSTANCE_ID || randomUUID(),
  PANINI_BOOTSTRAP_TOKEN: process.env.PANINI_BOOTSTRAP_TOKEN || token(),
  PANINI_CONSOLE_SECRET: process.env.PANINI_CONSOLE_SECRET || token(),
  // Gateway-to-backend only; never leaves this process tree.
  PANINI_CONSOLE_TOKEN: token(),
};
const SECRETS = new Set([...Object.keys(launch), 'PANINI_DESKTOP_SECRET']);
const withoutSecrets = (env) =>
  Object.fromEntries(Object.entries(env).filter(([key]) => !SECRETS.has(key)));

const backendExecutable = process.env.PANINI_BACKEND_EXECUTABLE;
const python =
  process.env.PYTHON ||
  join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const [backendCommand, backendArgs] = backendExecutable
  ? [backendExecutable, []]
  : [python, ['-m', 'backend.app']];
if (!existsSync(backendCommand))
  throw new Error('The Python environment is missing. Run npm run setup.');

const release = production
  ? snapshotRelease(root, { immutable: Boolean(backendExecutable) })
  : undefined;
const searchFetch = release ? (await import(release.entry)).default.fetch : undefined;
// PANINI-SIGNIN runs as a supervised child; without it PANINI stays locked (fail closed).
const signinDir = (process.env.PANINI_SIGNIN_DIR || '').trim();
const signinPort = Number(process.env.PANINI_SIGNIN_PORT || 3240);
const signin = createSigninGate({ url: signinDir ? `http://127.0.0.1:${signinPort}` : '' });
const gateway = createGateway({
  backendUrl,
  searchUrl,
  searchFetch,
  searchClientRoot: release?.clientRoot,
  publicRoot: release?.publicRoot,
  production,
  searchConfigured,
  allowedOrigins,
  signin,
  consoleToken: launch.PANINI_CONSOLE_TOKEN,
});
const children = [];
const discovery = runtimeFile();
let stopping = false;

function run(name, command, args, cwd, env) {
  const child = spawn(command, args, {
    cwd,
    windowsHide: true,
    stdio: ['ignore', 'pipe', 'pipe'],
    env: { ...env, PYTHONUNBUFFERED: '1', ANONYMIZED_TELEMETRY: 'false' },
  });
  children.push(child);
  child.stdout.on('data', (data) => process.stdout.write(`[${name}] ${data}`));
  child.stderr.on('data', (data) => process.stderr.write(`[${name}] ${data}`));
  child.on('error', (error) => {
    console.error(`[${name}] ${error.message}`);
    shutdown(1);
  });
  child.on('exit', (code) => {
    if (!stopping) {
      console.error(`[${name}] exited (${code})`);
      shutdown(1);
    }
  });
}

async function stopChild(child) {
  if (!child.pid || child.exitCode !== null) return;
  if (process.platform === 'win32') {
    await new Promise((resolve) => {
      const killer = spawn('taskkill', ['/PID', String(child.pid), '/T', '/F'], {
        windowsHide: true,
        stdio: 'ignore',
      });
      killer.on('exit', resolve);
      killer.on('error', resolve);
    });
  } else {
    child.kill('SIGTERM');
    await Promise.race([
      new Promise((resolve) => child.once('exit', resolve)),
      new Promise((resolve) => setTimeout(resolve, 5000)),
    ]);
    if (child.exitCode === null) child.kill('SIGKILL');
  }
}

async function shutdown(code = 0) {
  if (stopping) return;
  stopping = true;
  discovery.remove();
  release?.dispose();
  await gateway.close();
  await Promise.all(children.map(stopChild));
  process.exit(code);
}

process.on('SIGINT', () => shutdown());
process.on('SIGTERM', () => shutdown());
// Under the PANINI shell, the RuntimeHost job object ends this whole tree when the shell exits.
process.on('uncaughtException', (error) => {
  console.error(error);
  shutdown(1);
});
process.on('unhandledRejection', (error) => {
  console.error(error);
  shutdown(1);
});

await new Promise((resolve, reject) => {
  gateway.server.once('error', reject);
  gateway.server.listen(port, '127.0.0.1', resolve);
});
if (signinDir && existsSync(join(signinDir, 'server.mjs'))) {
  run('signin', process.execPath, ['--env-file-if-exists=.env', 'server.mjs'], signinDir, {
    ...withoutSecrets(process.env),
    PORT: String(signinPort),
    PANINI_PUBLIC_ORIGIN: `http://localhost:${port}`,
  });
} else {
  console.error('[signin] PANINI_SIGNIN_DIR is missing or invalid. PANINI stays locked until it is set.');
}
run('backend', backendCommand, backendArgs, root, {
  ...withoutSecrets(process.env),
  BACKEND_PORT: String(backendPort),
  PANINI_RUNTIME_ROOT: root,
  PANINI_CAPABILITY_ROUTER_ENABLED: 'true',
  PANINI_INSTANCE_ID: launch.PANINI_INSTANCE_ID,
  PANINI_BOOTSTRAP_TOKEN: launch.PANINI_BOOTSTRAP_TOKEN,
  PANINI_CONSOLE_TOKEN: launch.PANINI_CONSOLE_TOKEN,
  ...(process.env.PANINI_DESKTOP_SECRET
    ? { PANINI_DESKTOP_SECRET: process.env.PANINI_DESKTOP_SECRET }
    : {}),
});
if (!production) {
  const require = createRequire(import.meta.url);
  const vite = join(dirname(require.resolve('vite/package.json')), 'bin/vite.js');
  run(
    'search',
    process.execPath,
    [vite, '--port', String(searchPort)],
    join(root, 'panini-core/capabilities/search'),
    withoutSecrets(process.env),
  );
}
discovery.write({ pid: process.pid, port, console_secret: launch.PANINI_CONSOLE_SECRET });
const link = `http://localhost:${port}/signin`;
console.log(
  `PANINI runtime on http://localhost:${port} (${production ? 'production' : 'development'})`,
);
if (!managed) {
  console.log(`Web console (sign in with your face and voice): ${link}`);
}
console.log('Service readiness: /api/health');
