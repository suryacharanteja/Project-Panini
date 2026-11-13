import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdir } from 'node:fs/promises';
import { chromium } from 'playwright';

const origin = 'http://127.0.0.1:3240';
const headers = { 'x-panini-console': 'android-preview-only' };
const evidence = 'artifacts/android-hands';
const server = spawn('.venv/Scripts/python.exe', ['scripts/panini-android-preview.py'], {
  windowsHide: true,
  stdio: ['ignore', 'pipe', 'pipe'],
});
let diagnostics = '',
  browser;
for (const stream of [server.stdout, server.stderr])
  stream.on('data', (data) => (diagnostics = (diagnostics + data).slice(-5000)));
const request = async (path, method = 'GET', body) => {
  const response = await fetch(origin + path, {
    method,
    headers: { ...headers, 'Content-Type': 'application/json' },
    ...(body ? { body: JSON.stringify(body) } : {}),
  });
  assert(response.ok, await response.clone().text());
  return response.json();
};
try {
  let ready = false;
  for (let i = 0; i < 100; i++) {
    try {
      ready = (await fetch(origin + '/api/harness/android', { headers })).ok;
    } catch {}
    if (ready) break;
    if (server.exitCode !== null) throw Error(diagnostics);
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  assert(ready, diagnostics);
  assert.equal((await fetch(origin + '/api/harness/android')).status, 401);
  browser = await chromium.launch({ channel: 'chrome', headless: true });
  const page = await browser.newPage({
    viewport: { width: 1440, height: 1000 },
    extraHTTPHeaders: headers,
  });
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await page.goto(origin + '/build/android');
  await page.getByRole('button', { name: 'Find connected phones' }).click();
  await page.getByLabel('Selected Android phone').selectOption('phone');
  await page.getByLabel('I allow Playwright', { exact: false }).check();
  await page.getByLabel('Allowed app packages', { exact: false }).fill('com.example.notes');
  await page.getByRole('button', { name: 'Save setup', exact: true }).click();
  await page.waitForFunction(async () => {
    const r = await fetch('/api/harness/android', {
      headers: { 'x-panini-console': 'android-preview-only' },
    });
    const s = await r.json();
    return s.serial === 'phone' && s.driver_consent;
  });
  await page.getByLabel('Pairing address').fill('192.168.1.10:12345');
  await page.getByLabel('Pairing code').fill('123456');
  await page.getByRole('button', { name: 'Pair phone', exact: true }).click();
  await page.waitForFunction(
    () => document.querySelector('input[aria-label="Pairing code"]').value === '',
  );
  const setup = await request('/preview/state');
  assert(!JSON.stringify(setup).includes('123456'));
  await mkdir(evidence, { recursive: true });
  await page.screenshot({ path: evidence + '/admin-desktop.png', fullPage: true });
  await page.setViewportSize({ width: 412, height: 915 });
  await page.waitForFunction(
    () => document.querySelector('aside').getBoundingClientRect().right <= 0,
  );
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
  await page.screenshot({ path: evidence + '/admin-mobile.png', fullPage: true });
  await request('/preview/use', 'POST', {});
  assert((await request('/api/harness/status')).android.active);
  await page.getByRole('button', { name: 'Watch phone', exact: true }).click();
  await page.waitForFunction(() =>
    [...document.images].some((img) => img.src.startsWith('data:image') && img.naturalWidth),
  );
  await page.screenshot({ path: evidence + '/admin-live.png', fullPage: true });
  // Use the shipped phone Home module and the real status/takeover routes, with an authenticated loopback adapter.
  await page.goto(origin + '/phone/');
  await page.evaluate(async () => {
    const { home } = await import('/phone/screens/home.mjs');
    const app = {
      fabric: {
        request: async (method, path) => {
          const r = await fetch('/api/harness' + path, {
            method,
            headers: { 'x-panini-console': 'android-preview-only' },
          });
          return { status: r.status, body: await r.json() };
        },
      },
      show: () => {},
    };
    document.body.replaceChildren(await home(app));
  });
  await page.getByRole('heading', { name: 'PANINI is using your phone' }).waitFor();
  await page.screenshot({ path: evidence + '/android-home.png', fullPage: true });
  await page.getByRole('button', { name: 'Stop phone control', exact: true }).click();
  await page.getByRole('status').filter({ hasText: 'Phone control stopped.' }).waitFor();
  assert.equal((await request('/api/harness/status')).android.active, false);
  assert.deepEqual(errors, []);
  console.log(
    'Android Admin and PWA smoke passed: owner setup, secret clearing, responsive screens, registry effect, real live frames and phone takeover. Fake phone transport; physical Android pending.',
  );
} finally {
  await browser?.close();
  server.kill();
}
