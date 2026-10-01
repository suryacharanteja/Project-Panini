/**
 * The PANINI shell and the web console are one product: while the avatar holds the
 * conversation, the signed-in console stays fully usable on the same runtime.
 * No provider calls, microphone or desktop input. Run: npm run panini:test:unified
 */
import { _electron as electron } from 'playwright';
import { createRequire } from 'node:module';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';

const require = createRequire(import.meta.url);
const root = resolve(import.meta.dirname, '../..');
// Pairings made by this check go to a throwaway directory, never the real profile.
const env = {
  ...process.env,
  PANINI_NODE: process.execPath,
  PANINI_DATA_DIR: mkdtempSync(join(tmpdir(), 'panini-unified-')),
};
delete env.ELECTRON_RUN_AS_NODE;
const checks = [];
const check = (condition, label) => {
  if (!condition) throw new Error(label);
  checks.push(label);
  console.log('PASS', label);
};

const application = await electron.launch({
  executablePath: require('electron'),
  args: [resolve(root, 'panini-core/apps/shell')],
  env,
  timeout: 60000,
});
try {
  const page = await application.firstWindow();
  let status;
  for (let attempt = 0; attempt < 150; attempt++) {
    status = await page.evaluate(() => window.panini.invoke('status'));
    if (status.connected && status.snapshot?.state === 'ready') break;
    await new Promise((done) => setTimeout(done, 500));
  }
  check(
    status?.snapshot?.state === 'ready',
    'shell launches the unified runtime and holds the lease',
  );
  const capabilities = await page.evaluate(() => window.panini.invoke('capabilities'));
  check(
    ['browser', 'search', 'desktop', 'code'].every((name) =>
      capabilities.capabilities.some((cap) => cap.capability === name),
    ),
    'every capability is reported by the shared runtime',
  );

  // Capture outward actions instead of opening the default browser or a modal dialog.
  await application.evaluate(({ shell, dialog }) => {
    shell.openExternal = async (url) => {
      globalThis.paniniOpened = url;
    };
    dialog.showMessageBox = async (_window, options) => {
      globalThis.paniniDialog = options.message;
      return { response: 1 };
    };
  });
  await page.evaluate(() => window.panini.invoke('dashboard', { shareCaptions: false }));
  const url = await application.evaluate(() => globalThis.paniniOpened);
  check(
    /\/auth\/login\?ticket=v1\./.test(url),
    'dashboard opens through a one-time console sign-in',
  );
  const opened = application.waitForEvent('window');
  await application.evaluate(async ({ BrowserWindow }, target) => {
    const viewer = new BrowserWindow({
      width: 1100,
      height: 900,
      show: true,
      webPreferences: { nodeIntegration: false, contextIsolation: true, sandbox: true },
    });
    await viewer.loadURL(target);
  }, url);
  const viewer = await opened;
  await viewer.getByText('Connected to PANINI.', { exact: false }).waitFor({ timeout: 20000 });
  check(true, 'signed-in console follows the live PANINI conversation');
  const console = await viewer.evaluate(async () => {
    const extension = await fetch('/api/browser/extension/status');
    const pairing = await fetch('/api/extension/pairing-code', { method: 'POST' });
    const home = await fetch('/', { headers: { accept: 'text/html' } });
    return { extension: extension.status, pairing: pairing.status, home: home.status };
  });
  check(
    console.extension === 200 && console.pairing === 200 && console.home === 200,
    'web console stays fully usable while the shell controls PANINI',
  );
  const blocked = await viewer.evaluate(async () => {
    const session = JSON.parse(sessionStorage.getItem('panini.activity'));
    const response = await fetch(`/api/panini/sessions/${session.sessionId}/stop`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ command_id: crypto.randomUUID() }),
    });
    return response.status;
  });
  check(
    blocked === 401 || blocked === 403,
    'the console observes the conversation but cannot control it',
  );

  await page.evaluate(() => window.panini.invoke('pair'));
  const message = await application.evaluate(() => globalThis.paniniDialog);
  const code = /([A-Z0-9]{4}-[A-Z0-9]{4})/.exec(message)?.[1];
  check(Boolean(code), 'shell shows a one-time extension pairing code');
  const redeemed = await fetch(`${new URL(url).origin}/api/extension/pair`, {
    method: 'POST',
    headers: { 'content-type': 'application/json', origin: 'chrome-extension://unifiedtest' },
    body: JSON.stringify({ code }),
  });
  check(redeemed.status === 200, 'the extension can redeem the code the shell showed');
  const devices = await viewer.evaluate(async () => (await fetch('/api/extension/devices')).json());
  check(devices.devices.length === 1, 'the console sees the pairing the shell created');
} finally {
  await application.close();
}
console.log(`PANINI unified shell check passed (${checks.length} checks).`);

