/**
 * Whole-product test for UAT: one command that starts the full PANINI runtime (gateway, backend, search and
 * the native desktop broker) with every non-security v2 feature on, checks every part together, then runs
 * the existing end-to-end smokes in sequence.
 *
 *   npm run product:test                 # integrated checks, then every smoke
 *   npm run product:test -- --quick      # integrated checks only
 *   npm run product:test -- --only integrated,phone,personas
 *   npm run product:test -- --no-desktop # skip smokes that use the mouse and keyboard on test windows
 *   npm run product:test -- --live       # also the live-provider checks (needs a funded model account)
 *
 * Isolation: a throwaway data folder and its own ports and secrets. Model and speech API keys are blanked, so
 * nothing spends money or reaches your accounts; your running PANINI, data and pairings are untouched.
 * Security features (guardian, red-team enforcement) stay off, as the owner decided.
 * Report: artifacts/product-test/report.json and report.md, plus a screenshot of every Admin page.
 */
import { spawn, spawnSync } from 'node:child_process';
import { randomBytes, randomUUID } from 'node:crypto';
import { existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { mintTicket } from '../frontend/server/console-auth.mjs';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const args = process.argv.slice(2);
const quick = args.includes('--quick');
const only = (args[args.indexOf('--only') + 1] || '').split(',').filter(Boolean);
const out = join(root, 'artifacts/product-test');
mkdirSync(join(out, 'pages'), { recursive: true });

// Every non-security v2 feature on. Guardian and red-team enforcement are security work, kept aside.
export const FLAGS = {
  PANINI_V2_REGISTRY: 'true',
  PANINI_V2_POLICY: 'enforce',
  PANINI_V2_LEDGER: 'true',
  PANINI_V2_INTERRUPTS: 'true',
  PANINI_V2_ONE_BRAIN: 'true',
  PANINI_V2_AGUI: 'true',
  PANINI_BROWSER_DRIVER: 'playwright',
  PANINI_CLAW_CDP: 'true',
  PANINI_SANDBOX: 'true',
  PANINI_LIVE_VIEW: 'true',
  PANINI_REMOTE_MODE: 'relay', // No relay address: remote access reports needs_setup and opens no listener.
  PANINI_PHONE_LINE: 'true',
  PANINI_CHANNELS: 'slack,telegram,whatsapp,email',
  PANINI_PERSONAS: 'true',
  PANINI_SPACES: 'true',
  PANINI_OUTBOUND_CALLS: 'true',
  PANINI_ANDROID: 'true',
  PANINI_TEACH: 'true',
  PANINI_MCP_SERVER: 'true',
  PANINI_PRESENCE: 'true',
  PANINI_TRIGGERS: 'true',
  PANINI_DESKTOP_TOOLS: 'fine',
  PANINI_LOCAL_MODE: 'fallback',
};
const KEPT_ASIDE = { PANINI_GUARDIAN: 'false', PANINI_REDTEAM_ENFORCE: 'false' };

const results = [];
function record(group, name, passed, detail = '') {
  results.push({ group, name, passed, detail: String(detail).slice(0, 400) });
  console.log(
    `${passed ? 'PASS' : 'FAIL'}  [${group}] ${name}${detail ? ` — ${String(detail).slice(0, 200)}` : ''}`,
  );
}
async function check(group, name, body) {
  try {
    const detail = await body();
    record(group, name, true, detail || '');
  } catch (error) {
    record(group, name, false, error?.message || error);
  }
}
const sleep = (ms) => new Promise((done) => setTimeout(done, ms));
function assert(condition, message) {
  if (!condition) throw new Error(message);
}

// ---------------------------------------------------------------------------------------------- runtime
const ports = { gateway: 3261, backend: 8261, search: 3291 };
const data = mkdtempSync(join(tmpdir(), 'panini-product-'));
const instance = randomUUID();
const secret = randomBytes(32).toString('base64url');
const desktopSecret = randomBytes(32).toString('base64url');
const native = (name) =>
  join(
    root,
    'panini-core/capabilities/desktop',
    `Panini.Desktop.${name}/bin/Release/net10.0-windows/Panini.Desktop.${name}.exe`,
  );
const environment = {
  ...process.env,
  ...FLAGS,
  ...KEPT_ASIDE,
  PANINI_DATA_DIR: data,
  PANINI_INSTANCE_ID: instance,
  PANINI_BOOTSTRAP_TOKEN: randomBytes(32).toString('base64url'),
  PANINI_CONSOLE_SECRET: secret,
  PANINI_PROFILE_ID: 'panini-product-test',
  FRONTEND_PORT: String(ports.gateway),
  BACKEND_PORT: String(ports.backend),
  SEARCH_PORT: String(ports.search),
  JEV_REV_DESKTOP_ENABLED: 'true',
  PANINI_DESKTOP_PIPE: `panini-product-${instance}`,
  PANINI_DESKTOP_SECRET: desktopSecret,
  PANINI_DESKTOP_WORKER: native('Worker'),
  PANINI_NODE: process.execPath,
  // Blank before .env loads (it never overrides): no paid model, speech or gateway calls from this test.
  OPENAI_API_KEY: '',
  AI_GATEWAY_API_KEY: '',
  ASSEMBLYAI_API_KEY: '',
  ANONYMIZED_TELEMETRY: 'false',
};
const children = [];
let logs = '';
function launch(name, command, commandArgs, env) {
  const child = spawn(command, commandArgs, {
    cwd: root,
    env,
    windowsHide: true,
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  child.stdout.on('data', (chunk) => (logs += `[${name}] ${chunk}`));
  child.stderr.on('data', (chunk) => (logs += `[${name}] ${chunk}`));
  children.push(child);
  return child;
}
function stopAll() {
  // Only the processes this test started (and their own children).
  for (const child of children.reverse())
    if (child.pid && child.exitCode === null)
      spawnSync('taskkill', ['/PID', String(child.pid), '/T', '/F'], {
        windowsHide: true,
        stdio: 'ignore',
      });
}
process.on('exit', stopAll);

const gateway = `http://127.0.0.1:${ports.gateway}`;
const backend = `http://127.0.0.1:${ports.backend}`;
let cookie = '';
async function api(path, options = {}) {
  const response = await fetch(gateway + path, {
    ...options,
    redirect: 'manual',
    headers: {
      cookie,
      ...(options.body ? { 'content-type': 'application/json' } : {}),
      ...options.headers,
    },
    signal: AbortSignal.timeout(options.timeout || 20000),
  });
  const text = await response.text();
  let body = text;
  try {
    body = JSON.parse(text);
  } catch {}
  return { status: response.status, body };
}

async function startRuntime() {
  for (const port of Object.values(ports)) {
    const busy = await fetch(`http://127.0.0.1:${port}/`, {
      signal: AbortSignal.timeout(500),
    }).then(
      () => true,
      () => false,
    );
    assert(!busy, `Port ${port} is already in use; stop whatever uses it and retry.`);
  }
  if (existsSync(native('Broker')))
    launch('broker', native('Broker'), [], { ...environment, OPENAI_API_KEY: '' });
  launch('runtime', process.execPath, [join(root, 'frontend/server/main.mjs')], environment);
  for (let attempt = 0; attempt < 240; attempt++) {
    const health = await Promise.all(
      [gateway, backend].map((origin) =>
        fetch(`${origin}/api/panini/health`, { signal: AbortSignal.timeout(1000) })
          .then((r) => (r.ok ? r.json() : null))
          .catch(() => null),
      ),
    );
    if (health.every(Boolean)) return health;
    if (children.some((c) => c.exitCode !== null))
      throw new Error(`The runtime exited.\n${logs.slice(-3000)}`);
    await sleep(500);
  }
  throw new Error(`The runtime did not become healthy in 120 s.\n${logs.slice(-3000)}`);
}

// ------------------------------------------------------------------------------------- integrated checks
async function integrated() {
  let health;
  await check('runtime', 'gateway and backend start healthy as one instance', async () => {
    health = await startRuntime();
    assert(
      health.every((h) => h.instance_id === instance),
      `instance ids ${health.map((h) => h.instance_id)}`,
    );
    return `instance ${instance.slice(0, 8)}`;
  });
  if (!health) return;
  await check('runtime', 'owner signs in to the console with a one-time ticket', async () => {
    const response = await fetch(`${gateway}/auth/login?ticket=${mintTicket(secret)}`, {
      redirect: 'manual',
    });
    cookie = (response.headers.getSetCookie?.() || [response.headers.get('set-cookie')])
      .filter(Boolean)
      .map((c) => c.split(';')[0])
      .join('; ');
    assert(cookie, `no session cookie (status ${response.status})`);
  });
  await check('flags', 'every non-security v2 feature is on and nothing is blocked', async () => {
    const { status, body } = await api('/api/harness/v2/status');
    assert(status === 200, `status ${status}`);
    const wrong = Object.entries({ ...FLAGS, ...KEPT_ASIDE }).filter(
      ([k, v]) => body.flags[k] !== v,
    );
    assert(
      !wrong.length,
      `flags differ: ${wrong.map(([k]) => `${k}=${body.flags[k]}`).join(', ')}`,
    );
    assert(!Object.keys(body.gate.blocked).length, `blocked: ${JSON.stringify(body.gate.blocked)}`);
    return `${Object.keys(FLAGS).length} on; guardian and red-team enforcement kept aside`;
  });

  // Every console API the Admin pages read answers without a server error, with all features on together.
  const listed = spawnSync(
    join(root, '.venv/Scripts/python.exe'),
    [join(root, 'scripts/panini-product-routes.py')],
    {
      cwd: root,
      encoding: 'utf8',
      windowsHide: true,
      env: { ...environment, PANINI_DATA_DIR: join(data, 'routes') },
    },
  );
  const routes = JSON.parse(listed.stdout || '[]').filter(
    (path) =>
      /^\/api\/(admin\/v1|harness)\//.test(path) &&
      // Effects or downloads, never part of a read sweep.
      !/\/(download|export|oauth\/callback)$/.test(path),
  );
  const sweep = [];
  for (const path of routes) {
    const started = Date.now();
    const query = { '/api/harness/spaces/search': '?q=panini' }[path] || ''; // Required parameters.
    const { status, body } = await api(path + query, { timeout: 30000 }).catch((error) => ({
      status: 0,
      body: error.message,
    }));
    sweep.push({ path, status, ms: Date.now() - started });
    // 503 is an honest "needs setup" (no Docker, no ADB, no account); anything else 5xx or missing is a defect.
    const ok =
      (status >= 200 && status < 300) || (status === 503 && body?.detail) || status === 409;
    if (!ok)
      record(
        'api',
        `GET ${path}`,
        false,
        `status ${status}: ${JSON.stringify(body).slice(0, 200)}`,
      );
  }
  writeFileSync(join(out, 'api-sweep.json'), JSON.stringify(sweep, null, 1));
  const failed = results.filter((r) => r.group === 'api' && !r.passed).length;
  record(
    'api',
    `${routes.length} console APIs answer with every feature on`,
    routes.length > 50 && failed === 0,
    `${routes.length - failed}/${routes.length} ok; ${sweep.filter((r) => r.status === 503).length} report needs-setup`,
  );

  await check('desktop', 'backend lists Windows through the native broker and worker', async () => {
    const { status, body } = await api('/api/harness/teach/windows');
    assert(status === 200, `status ${status}: ${JSON.stringify(body).slice(0, 200)}`);
    const windows = body.windows || body;
    assert(Array.isArray(windows), 'no window list');
    return `${windows.length} windows`;
  });

  await check('ledger', 'receipt chain verifies after the whole sweep', async () => {
    const { status, body } = await api('/api/harness/ledger/verify');
    assert(
      status === 200 && body.ok !== false && body.valid !== false,
      JSON.stringify(body).slice(0, 200),
    );
  });

  await adminPages();
}

const PAGES = [
  '/',
  '/inbox',
  '/conversations',
  '/runs',
  '/receipts',
  '/projects',
  '/memory',
  '/spaces',
  '/audit',
  '/playground',
  '/build/agents',
  '/build/personas',
  '/build/skills',
  '/build/automations',
  '/build/channels',
  '/build/phone',
  '/build/remote',
  '/build/android',
  '/build/computers',
  '/build/connectors',
  '/build/capabilities',
  '/build/live',
  '/build/safety',
  '/capabilities/browser',
  '/capabilities/code',
  '/capabilities/desktop',
  '/capabilities/search',
  '/capabilities/voice',
  '/settings/keys',
  '/settings/devices',
  '/settings/learning',
  '/settings/system',
  '/settings/usage',
];

async function adminPages() {
  const { chromium } = await import('playwright');
  const browser = await chromium.launch({ headless: true, channel: 'chrome' });
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    const page = await context.newPage();
    await page.goto(`${gateway}/auth/login?ticket=${mintTicket(secret)}`);
    for (const path of PAGES) {
      await check('admin', `page ${path} loads directly and renders`, async () => {
        const errors = [];
        const onError = (error) => errors.push(error.message);
        page.on('pageerror', onError);
        try {
          const response = await page.goto(gateway + path, { waitUntil: 'domcontentloaded' });
          assert(response?.status() === 200, `HTTP ${response?.status()}`);
          await page.waitForLoadState('networkidle', { timeout: 15000 }).catch(() => {});
          const text = (await page.locator('body').innerText()).trim();
          assert(text.length > 40, 'blank page');
          assert(
            !/something went wrong|unexpected application error/i.test(text),
            text.slice(0, 160),
          );
          const name = path === '/' ? 'home' : path.slice(1).replaceAll('/', '-');
          await page.screenshot({ path: join(out, 'pages', `${name}.png`) });
          assert(!errors.length, `page errors: ${errors.join(' | ').slice(0, 200)}`);
        } finally {
          page.off('pageerror', onError);
        }
      });
    }
  } finally {
    await browser.close();
  }
}

// ------------------------------------------------------------------------------------ end-to-end smokes
const py = join(root, '.venv/Scripts/python.exe');
const isolatedPorts = {
  FRONTEND_PORT: String(ports.gateway),
  BACKEND_PORT: String(ports.backend),
  SEARCH_PORT: String(ports.search),
};
const noKeys = { OPENAI_API_KEY: '', AI_GATEWAY_API_KEY: '', ASSEMBLYAI_API_KEY: '' };
// [id, area, command, args, options]. needs: desktop (real input on the test's own windows), live (a
// funded model account), packaged (a built release). Each runs on its own fixtures and throwaway data.
const SMOKES = [
  [
    'shell',
    'Avatar shell (Electron) with its runtime',
    process.execPath,
    ['panini-core/apps/shell/test/smoke.mjs'],
    {
      env: {
        ...isolatedPorts,
        ...noKeys,
        PANINI_SMOKE_USER_DATA: 'TEMP:shell-profile',
        PANINI_DATA_DIR: 'TEMP:shell-data',
      },
    },
  ],
  [
    'runtime',
    'Unified runtime: gateway, backend, search, pairing',
    py,
    ['scripts/runtime-smoke.py'],
    { env: { ...isolatedPorts, ...noKeys, PANINI_DATA_DIR: 'TEMP:runtime-data' } },
  ],
  [
    'evals',
    'Scorecard (offline golden tasks, incl. personas)',
    py,
    ['scripts/panini-eval.py', 'run', '--mode', 'offline', '--fail-under', '1.0'],
  ],
  [
    'phone',
    'Phone app (Android PWA), remote fabric, calls, push',
    process.execPath,
    ['scripts/panini-phone-smoke.mjs'],
  ],
  [
    'continuity',
    'Continue on another device (V2-15)',
    process.execPath,
    ['scripts/panini-continuity-smoke.mjs'],
  ],
  ['personas', 'Personas Admin (V2-18)', process.execPath, ['scripts/panini-persona-smoke.mjs']],
  [
    'personas-ui',
    'Persona identity on the avatar (V2-18)',
    process.execPath,
    ['scripts/panini-persona-ui-smoke.mjs'],
  ],
  [
    'channels',
    'Channels: Telegram/Slack/WhatsApp/email Admin and intake (V2-17)',
    process.execPath,
    ['scripts/panini-channels-smoke.mjs'],
  ],
  [
    'spaces',
    'Spaces notebook (V2-19)',
    process.execPath,
    ['scripts/panini-spaces-smoke.mjs'],
    { server: ['scripts/panini-spaces-dev.py', 3223] },
  ],
  [
    'outbound',
    'Outbound calls from reviewed briefs (V2-20)',
    process.execPath,
    ['scripts/panini-outbound-smoke.mjs'],
  ],
  [
    'android',
    'Android hand: setup, live frames, takeover (V2-21)',
    process.execPath,
    ['scripts/panini-android-smoke.mjs'],
  ],
  [
    'android-driver',
    'Pinned Android transport loads',
    py,
    ['scripts/panini-android-driver-smoke.py'],
  ],
  [
    'teach',
    'Teach by demonstration in the browser (V2-22)',
    process.execPath,
    ['scripts/panini-teach-smoke.mjs'],
  ],
  ['teach-ui', 'Teach Admin UI (V2-22)', process.execPath, ['scripts/panini-teach-ui-smoke.mjs']],
  ['mcp-ui', 'MCP server Admin (V2-23)', process.execPath, ['scripts/panini-mcp-ui-smoke.mjs']],
  [
    'triggers-ui',
    'Triggers and responsibilities Admin (V2-25)',
    process.execPath,
    ['scripts/panini-triggers-ui-smoke.mjs'],
  ],
  [
    'local-mode-ui',
    'Local model settings (V2-27)',
    process.execPath,
    ['scripts/panini-local-mode-ui-smoke.mjs'],
  ],
  [
    'local-tts',
    'Offline speech in the shell (V2-27)',
    process.execPath,
    ['scripts/panini-local-tts-shell-smoke.mjs'],
  ],
  [
    'desktop-native',
    'Windows broker/worker on a disposable fixture',
    py,
    ['scripts/panini-native-smoke.py'],
    { needs: 'desktop' },
  ],
  [
    'desktop-cancel',
    'Owned process cleanup and broker cancellation',
    py,
    ['scripts/panini-runtime-smoke.py'],
    { needs: 'desktop' },
  ],
  [
    'desktop-fine',
    'Fine desktop tools on a disposable fixture (V2-26)',
    py,
    ['scripts/panini-desktop-fine-smoke.py'],
    { needs: 'desktop' },
  ],
  [
    'desktop-notepad',
    'Fine tools in real Notepad, disposable file (V2-26)',
    py,
    ['scripts/panini-desktop-notepad-smoke.py'],
    { needs: 'desktop' },
  ],
  [
    'teach-desktop',
    'Teach on Windows: record, review, replay, drag (V2-22)',
    py,
    ['scripts/panini-teach-desktop-smoke.py'],
    { needs: 'desktop' },
  ],
  [
    'teach-notepad',
    'Teach in Notepad incl. Save As (V2-22)',
    py,
    ['scripts/panini-teach-notepad-smoke.py'],
    { needs: 'desktop' },
  ],
  [
    'paint',
    'Drawing in Paint through the real broker (V2-26)',
    py,
    ['scripts/panini-paint-smoke.py'],
    { needs: 'desktop' },
  ],
  [
    'voice-live',
    'Persona voices on a live provider',
    py,
    ['scripts/panini-persona-voice-live.py'],
    { needs: 'live' },
  ],
  [
    'team-live',
    'Research-to-writing team on a live provider',
    py,
    ['scripts/panini-persona-team-live.py'],
    { needs: 'live' },
  ],
  [
    'spaces-live',
    'Notebook citations on a live provider',
    py,
    ['scripts/panini-spaces-live.py'],
    { needs: 'live' },
  ],
  [
    'sandbox-live',
    'Sandbox computers on Docker (V2-11)',
    py,
    ['scripts/panini-sandbox-live.py'],
    { needs: 'live', why: 'needs Docker Desktop with the sandbox images built' },
  ],
  [
    'mcp-packaged',
    'Packaged MCP transports',
    py,
    ['scripts/panini-mcp-packaged-smoke.py'],
    { needs: 'packaged' },
  ],
];

function runCommand(command, commandArgs, env, timeoutMs) {
  return new Promise((done) => {
    const child = spawn(command, commandArgs, {
      cwd: root,
      env,
      windowsHide: true,
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    let output = '';
    child.stdout.on('data', (chunk) => (output = (output + chunk).slice(-20000)));
    child.stderr.on('data', (chunk) => (output = (output + chunk).slice(-20000)));
    const timer = setTimeout(() => {
      spawnSync('taskkill', ['/PID', String(child.pid), '/T', '/F'], {
        windowsHide: true,
        stdio: 'ignore',
      });
      output += `\nTimed out after ${timeoutMs / 1000} s.`;
    }, timeoutMs);
    child.on('exit', (code) => {
      clearTimeout(timer);
      done({ code, output });
    });
    child.on('error', (error) => {
      clearTimeout(timer);
      done({ code: -1, output: error.message });
    });
  });
}

async function smokes() {
  const scratch = mkdtempSync(join(tmpdir(), 'panini-product-smokes-'));
  const wanted = SMOKES.filter(([id]) => !only.length || only.includes(id));
  if (wanted.some(([, , , , o]) => o?.needs === 'desktop') && !args.includes('--no-desktop'))
    console.log(
      '\nDesktop smokes use real mouse and keyboard on their own disposable windows. Hands off for a few minutes.\n',
    );
  try {
    for (const [id, area, command, commandArgs, options = {}] of wanted) {
      const skip =
        (options.needs === 'desktop' &&
          args.includes('--no-desktop') &&
          'skipped (--no-desktop)') ||
        (options.needs === 'live' &&
          !args.includes('--live') &&
          `skipped: ${options.why || 'needs a funded model account'} (run with --live)`) ||
        (options.needs === 'packaged' &&
          !args.includes('--packaged') &&
          'skipped: needs a packaged build (run with --packaged)');
      if (skip) {
        results.push({ group: 'smoke', name: `${id}: ${area}`, passed: null, detail: skip });
        console.log(`SKIP  [smoke] ${id}: ${area} — ${skip}`);
        continue;
      }
      const env = { ...process.env };
      for (const [key, value] of Object.entries(options.env || {}))
        env[key] = value.startsWith('TEMP:') ? join(scratch, value.slice(5)) : value;
      let server;
      if (options.server) {
        server = spawn(py, [options.server[0]], {
          cwd: root,
          env,
          windowsHide: true,
          stdio: 'ignore',
        });
        for (let i = 0; i < 120; i++) {
          const up = await fetch(`http://127.0.0.1:${options.server[1]}/`, {
            signal: AbortSignal.timeout(500),
          }).then(
            () => true,
            () => false,
          );
          if (up) break;
          await sleep(250);
        }
      }
      const started = Date.now();
      const { code, output } = await runCommand(command, commandArgs, env, 15 * 60 * 1000);
      if (server)
        spawnSync('taskkill', ['/PID', String(server.pid), '/T', '/F'], {
          windowsHide: true,
          stdio: 'ignore',
        });
      writeFileSync(join(out, `smoke-${id}.log`), output);
      const seconds = Math.round((Date.now() - started) / 1000);
      const last = output.trim().split('\n').filter(Boolean).at(-1) || '';
      record(
        'smoke',
        `${id}: ${area}`,
        code === 0,
        `${seconds}s · ${code === 0 ? last.slice(0, 160) : last.slice(0, 300)}`,
      );
    }
  } finally {
    rmSync(scratch, { recursive: true, force: true, maxRetries: 5, retryDelay: 500 });
  }
}

function report(started) {
  const passed = results.filter((r) => r.passed === true).length;
  const failed = results.filter((r) => r.passed === false);
  const skipped = results.filter((r) => r.passed === null).length;
  const summary = {
    passed,
    failed: failed.length,
    skipped,
    seconds: Math.round((Date.now() - started) / 1000),
  };
  writeFileSync(
    join(out, 'report.json'),
    JSON.stringify(
      { when: new Date().toISOString(), flags: FLAGS, kept_aside: KEPT_ASIDE, summary, results },
      null,
      1,
    ),
  );
  const rows = results.map(
    (r) =>
      `| ${r.passed === true ? 'PASS' : r.passed === false ? '**FAIL**' : 'skip'} | ${r.group} | ${r.name.replaceAll('|', '\\|')} | ${String(r.detail).replaceAll('|', '\\|').replaceAll('\n', ' ')} |`,
  );
  writeFileSync(
    join(out, 'report.md'),
    [
      '# PANINI whole-product test',
      '',
      `${new Date().toISOString()} · ${passed} passed, ${failed.length} failed, ${skipped} skipped · ${summary.seconds} s`,
      '',
      `Every non-security v2 feature on (${Object.keys(FLAGS).length} flags). Kept aside: guardian and red-team enforcement.`,
      'Throwaway data, own ports, model keys blanked. Admin page screenshots: `pages/`. Per-smoke logs: `smoke-*.log`.',
      '',
      '| Result | Area | Check | Detail |',
      '| --- | --- | --- | --- |',
      ...rows,
      '',
    ].join('\n'),
  );
  console.log(
    `\n${passed} passed, ${failed.length} failed, ${skipped} skipped in ${summary.seconds} s → artifacts/product-test/report.md`,
  );
  for (const r of failed) console.log(`  FAIL [${r.group}] ${r.name}`);
  return failed.length === 0;
}

const started = Date.now();
if (!only.length || only.includes('integrated')) {
  try {
    await integrated();
  } finally {
    stopAll();
    rmSync(data, { recursive: true, force: true, maxRetries: 5, retryDelay: 500 });
  }
}
if (!quick) await smokes();
process.exitCode = report(started) ? 0 : 1;
