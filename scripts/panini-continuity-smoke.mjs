// Real Electron shell + real gateway/backend, isolated local profile, no provider calls.
import { _electron as electron } from 'playwright';
import { createRequire } from 'node:module';
import { mkdir, writeFile } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { randomUUID } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { readFileSync, existsSync } from 'node:fs';

const require = createRequire(import.meta.url);
const root = resolve(import.meta.dirname, '..');
const output = join(root, 'artifacts/continuity');
const profile = join(output, 'profile-' + randomUUID());
const data = join(profile, 'data');
await mkdir(data, { recursive: true });
const seeded = JSON.parse(
  execFileSync(
    join(root, '.venv/Scripts/python.exe'),
    [
      '-c',
      'import json,sys; from backend.harness.store import Store; from backend.harness.sessions import Sessions; s=Store(sys.argv[1]); c=Sessions(s); t=c.create("Original continuity conversation"); c.save(t,[{"role":"user","content":"Keep this original turn"}]); print(json.dumps({"thread_id":t})); s.close()',
      join(data, 'harness.sqlite3'),
    ],
    { cwd: root, windowsHide: true, encoding: 'utf8' },
  ),
);
const env = {
  ...process.env,
  PANINI_NODE: process.execPath,
  PANINI_DATA_DIR: data,
  FRONTEND_PORT: '3247',
  BACKEND_PORT: '8247',
  SEARCH_PORT: '3248',
  ALLOWED_ORIGINS: 'http://127.0.0.1:3247,http://localhost:3247',
  PANINI_PRESENCE: 'true',
  PANINI_LIVE_VIEW: 'true',
  PANINI_REMOTE_MODE: 'off',
  PANINI_REDTEAM_ENFORCE: 'false',
  PANINI_CHANNELS: '',
  PANINI_PHONE_LINE: 'false',
  PANINI_OUTBOUND_CALLS: 'false',
  OPENAI_API_KEY: '',
  AI_GATEWAY_API_KEY: '',
  PANINI_JEV_GATES: 'false',
};
// Keep credential names from the local .env explicitly blank; never print or copy their values.
if (existsSync(join(root, '.env')))
  for (const line of readFileSync(join(root, '.env'), 'utf8').split(/\r?\n/)) {
    const name = line.match(/^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=/)?.[1];
    if (name && /(KEY|SECRET|TOKEN|PASSWORD|CREDENTIAL)/i.test(name)) env[name] = '';
  }
delete env.ELECTRON_RUN_AS_NODE;
const packaged = process.argv.includes('--packaged');
const application = await electron.launch({
  executablePath: packaged
    ? join(root, 'artifacts/panini-package/win-unpacked/PANINI.exe')
    : require('electron'),
  args: [...(packaged ? [] : [join(root, 'panini-core/apps/shell')]), '--user-data-dir=' + profile],
  env,
  timeout: 60000,
});
const errors = [],
  checks = [];
let runtimeDiagnostic = '';
application.process().stderr.on('data', (chunk) => {
  runtimeDiagnostic = (runtimeDiagnostic + chunk).slice(-20000);
});
try {
  const actualProfile = await application.evaluate(({ app }) => app.getPath('userData'));
  if (resolve(actualProfile) !== resolve(profile))
    throw Error('The native shell did not isolate its profile');
  checks.push('isolated native Electron profile and runtime data');
  const avatar = await application.firstWindow();
  avatar.on('pageerror', (error) => errors.push(error.message));
  await avatar.getByRole('heading', { name: 'Let’s get something done.', exact: true }).waitFor();
  // Playwright treats a Promise returned by waitForFunction as truthy before it resolves. Poll the
  // resolved IPC result instead: installed runtime hash verification can precede startup by seconds.
  let ready;
  for (let attempt = 0; attempt < 240; attempt++) {
    ready = await avatar.evaluate(() => window.panini.invoke('status'));
    if (ready.connected && ready.snapshot?.state === 'ready') break;
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  if (!ready?.connected || ready.snapshot?.state !== 'ready')
    throw Error('Native runtime did not become ready');
  await application.evaluate(({ shell }) => {
    shell.openExternal = async (url) => {
      globalThis.continuityAdminUrl = url;
    };
  });
  await avatar.evaluate(() => window.panini.invoke('admin', { page: '/settings/system' }));
  const adminOpened = application.waitForEvent('window');
  await application.evaluate(async ({ BrowserWindow }) => {
    const window = new BrowserWindow({
      width: 1440,
      height: 1000,
      show: true,
      webPreferences: { sandbox: true, contextIsolation: true, nodeIntegration: false },
    });
    await window.loadURL(globalThis.continuityAdminUrl);
  });
  const admin = await adminOpened;
  admin.on('pageerror', (error) => errors.push(error.message));
  await admin.getByText(/^Current presence:/).waitFor({ timeout: 20000 });
  let freshSignal = false;
  for (let attempt = 0; attempt < 80; attempt++) {
    freshSignal = await admin.evaluate(async () => {
      const value = await (await fetch('/api/harness/presence')).json();
      return value.signals?.some((row) => row.source === 'pc' && row.fresh);
    });
    if (freshSignal) break;
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  if (!freshSignal) throw Error('Native heartbeat did not arrive');
  await admin.reload();
  await admin.getByText(/^Current presence:/).waitFor();
  const signal = await admin.evaluate(async () => (await fetch('/api/harness/presence')).json());
  if (
    !signal.signals.some(
      (row) => row.source === 'pc' && row.fresh && row.data.avatar_visible && !row.data.locked,
    )
  )
    throw Error('Native heartbeat unavailable');
  checks.push('real Electron powerMonitor heartbeat reaches authenticated backend');
  await admin.getByLabel('Notification type', { exact: true }).selectOption('result');
  await admin.getByLabel('near_pc push delay seconds', { exact: true }).fill('150');
  await admin.getByLabel('Timezone', { exact: true }).fill('Asia/Dubai');
  await admin.getByRole('button', { name: 'Save continuity rules', exact: true }).click();
  await admin.getByText('Continuity rules saved.', { exact: true }).waitFor();
  const saved = await admin.evaluate(async () => (await fetch('/api/harness/presence')).json());
  if (
    saved.settings.rules.result.near_pc.find(([route]) => route === 'push')[1] !== 150 ||
    saved.settings.quiet.timezone !== 'Asia/Dubai'
  )
    throw Error('Admin rules did not persist');
  await admin.screenshot({
    path: join(output, packaged ? 'packaged-admin-desktop.png' : 'admin-desktop.png'),
    fullPage: true,
  });
  await admin.setViewportSize({ width: 412, height: 915 });
  await admin.waitForTimeout(100);
  const noOverflow = await admin.evaluate(() => document.documentElement.scrollWidth <= innerWidth);
  if (!noOverflow) throw Error('Continuity editor overflows the phone viewport');
  await admin.screenshot({
    path: join(output, packaged ? 'packaged-admin-mobile.png' : 'admin-mobile.png'),
    fullPage: true,
  });
  checks.push(
    'built Admin routing and quiet-hours editor saves real settings at desktop/mobile widths',
  );
  const queued = await admin.evaluate(async (thread_id) => {
    const response = await fetch('/api/harness/presence/handoff', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ thread_id, destination: 'pc' }),
    });
    if (!response.ok) throw Error(await response.text());
    return response.json();
  }, seeded.thread_id);
  if (queued.thread_id !== seeded.thread_id) throw Error('Wrong handoff thread');
  await avatar.getByLabel('Continued conversation', { exact: true }).waitFor({ timeout: 15000 });
  await avatar.getByText('Previous conversation', { exact: true }).click();
  await avatar.getByText('USER: Keep this original turn', { exact: true }).waitFor();
  const binding = JSON.parse(
    execFileSync(
      join(root, '.venv/Scripts/python.exe'),
      [
        '-c',
        'import json,sys; from backend.harness.store import Store; s=Store(sys.argv[1]); print(json.dumps({"binding":s.one("SELECT thread_id FROM thread_bindings WHERE surface=\'avatar\' ORDER BY created DESC LIMIT 1"),"sessions":s.one("SELECT COUNT(*) n FROM sessions")["n"]})); s.close()',
        join(data, 'harness.sqlite3'),
      ],
      { cwd: root, windowsHide: true, encoding: 'utf8' },
    ),
  );
  if (binding.binding.thread_id !== seeded.thread_id || binding.sessions !== 1)
    throw Error('Native handoff replaced the original thread');
  checks.push(
    'authenticated handoff restores original avatar agent binding and transcript without a new session',
  );
  await avatar.screenshot({
    path: join(output, packaged ? 'packaged-avatar-handoff.png' : 'avatar-handoff.png'),
  });
  for (let attempt = 0; attempt < 40 && application.windows().length < 3; attempt++)
    await new Promise((resolve) => setTimeout(resolve, 100));
  const live = application.windows().find((page) => page.url().includes('/build/live'));
  if (!live) throw Error('Live view did not open');
  await live.getByRole('heading', { name: 'Live', exact: true }).waitFor({ timeout: 15000 });
  checks.push('native handoff opens the actual Live view with its owner session');
  // Exercise the actual renderer/preload allowlist, rather than calling the owner HTTP API alone.
  await avatar.getByRole('button', { name: 'Send to my phone', exact: true }).click();
  let queuedPhone = false;
  for (let attempt = 0; attempt < 40; attempt++) {
    queuedPhone = await admin.evaluate(async () => {
      const value = await (await fetch('/api/harness/presence')).json();
      return value.deliveries.some(
        (row) => row.item_id.startsWith('handoff:') && row.route === 'push',
      );
    });
    if (queuedPhone) break;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  if (!queuedPhone) throw Error('Native send-to-phone did not persist its push route');
  const phoneThread = JSON.parse(
    execFileSync(
      join(root, '.venv/Scripts/python.exe'),
      [
        '-c',
        "import json,sys; from backend.harness.store import Store; s=Store(sys.argv[1]); print(json.dumps(s.all(\"SELECT i.thread_id FROM continuity_items i JOIN continuity_deliveries d ON d.item_id=i.id WHERE i.id LIKE 'handoff:%' AND d.route='push'\"))); s.close()",
        join(data, 'harness.sqlite3'),
      ],
      { cwd: root, windowsHide: true, encoding: 'utf8' },
    ),
  );
  if (phoneThread.length !== 1 || phoneThread[0].thread_id !== seeded.thread_id)
    throw Error('Native continuity IPC did not preserve the original thread');
  checks.push('actual renderer/preload continuity IPC queues the original thread for the phone');
  // Test notification acknowledgement failure against the real authenticated owner API. These
  // deliberately seeded notification records exercise UI/IPC semantics, not transport delivery.
  const seedNotification = async (id, title) =>
    application.evaluate(
      ({ BrowserWindow }, data) => {
        const native = BrowserWindow.getAllWindows().find(
          (window) => window.webContents.getURL() === 'panini://app/',
        );
        native.webContents.send('panini:event', { type: 'continuity.notification', data });
      },
      { id, item_id: 'result:' + id, title, interrupt: false },
    );
  await seedNotification('smoke-missing', 'Missing acknowledgement fixture');
  const missingCard = avatar
    .getByLabel('PANINI notification', { exact: true })
    .filter({ hasText: 'Missing acknowledgement fixture' });
  await missingCard.getByRole('button', { name: 'Seen', exact: true }).click();
  await avatar.getByRole('alert').waitFor();
  if (!(await missingCard.isVisible()))
    throw Error('Rejected acknowledgement hid the unresolved notification');
  await application.evaluate(({ BrowserWindow }) => {
    const native = BrowserWindow.getAllWindows().find(
      (window) => window.webContents.getURL() === 'panini://app/',
    );
    native.webContents.send('panini:event', {
      type: 'continuity.retract',
      data: { id: 'smoke-missing' },
    });
  });
  execFileSync(
    join(root, '.venv/Scripts/python.exe'),
    [
      '-c',
      'import sys,time; from backend.harness.store import Store; s=Store(sys.argv[1]); s.execute("INSERT INTO continuity_items(id,kind,title,thread_id,created,expires) VALUES (?,?,?,?,?,?)",("result:smoke-seen","result","Seen acknowledgement fixture",sys.argv[2],time.time(),time.time()+600)); s.close()',
      join(data, 'harness.sqlite3'),
      seeded.thread_id,
    ],
    { cwd: root, windowsHide: true },
  );
  await seedNotification('smoke-seen', 'Seen acknowledgement fixture');
  const seenCard = avatar
    .getByLabel('PANINI notification', { exact: true })
    .filter({ hasText: 'Seen acknowledgement fixture' });
  await seenCard.getByRole('button', { name: 'Seen', exact: true }).click();
  await seenCard.waitFor({ state: 'hidden' });
  const seenStatus = execFileSync(
    join(root, '.venv/Scripts/python.exe'),
    [
      '-c',
      'import sys; from backend.harness.store import Store; s=Store(sys.argv[1]); print(s.one("SELECT status FROM continuity_items WHERE id=?",("result:smoke-seen",))["status"]); s.close()',
      join(data, 'harness.sqlite3'),
    ],
    { cwd: root, windowsHide: true, encoding: 'utf8' },
  ).trim();
  if (seenStatus !== 'closed')
    throw Error('Seen card disappeared without a durable server resolution');
  checks.push(
    'Seen retains a rejected card and removes a valid fixture only after durable server resolution',
  );
  if (errors.length) throw Error(errors.join('\n'));
  const report = {
    passed: true,
    packaged,
    checks,
    errors,
    mobileNoOverflow: noOverflow,
    thread_id: seeded.thread_id,
    physicalAndroid: false,
    realSip: false,
  };
  await writeFile(
    join(output, packaged ? 'packaged-smoke.json' : 'native-smoke.json'),
    JSON.stringify(report, null, 2),
  );
  console.log(JSON.stringify(report));
} catch (error) {
  console.error(error);
  await writeFile(join(output, 'native-runtime-diagnostic.log'), runtimeDiagnostic);
  for (const [index, page] of application.windows().entries())
    await page.screenshot({ path: join(output, `failure-${index}.png`) }).catch(() => {});
  process.exitCode = 1;
} finally {
  await application.close();
}
