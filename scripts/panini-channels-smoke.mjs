import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdir } from 'node:fs/promises';
import { chromium } from 'playwright';

const origin = 'http://127.0.0.1:3235';
const headers = { 'x-panini-console': 'panini-channels-preview-only' };
const server = spawn('.venv/Scripts/python.exe', ['scripts/panini-channels-preview.py'], {
  windowsHide: true,
  stdio: ['ignore', 'pipe', 'pipe'],
});
let diagnostics = '',
  browser;
for (const stream of [server.stdout, server.stderr])
  stream.on('data', (data) => {
    diagnostics = (diagnostics + data).slice(-5000);
  });
try {
  let ready = false;
  for (let attempt = 0; attempt < 80; attempt++) {
    try {
      ready = (await fetch(`${origin}/api/admin/v1/channels`, { headers })).ok;
    } catch {}
    if (ready) break;
    if (server.exitCode !== null) throw new Error(`Preview failed: ${diagnostics}`);
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  assert(ready, diagnostics);
  browser = await chromium.launch({ headless: true, channel: 'chrome' });
  const page = await browser.newPage({
    viewport: { width: 1440, height: 1000 },
    extraHTTPHeaders: headers,
  });
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.goto(`${origin}/build/channels`);
  await page.getByRole('heading', { name: 'Messaging channels' }).waitFor();
  await page.getByLabel('Name', { exact: true }).fill('Owner preview');
  await page.getByLabel('Owner user IDs').fill('42');
  await page.getByLabel('Bot token').fill('123456:abcdefghijklmnopqrstuvw_xyz');
  await page.getByLabel('Enable this account').check();
  await page.getByRole('button', { name: 'Save account' }).click();
  await page.getByText('Owner preview', { exact: true }).waitFor();
  assert.equal(await page.getByLabel('Bot token').inputValue(), '');
  await fetch(`${origin}/preview/message`, { method: 'POST', headers });
  let replied = false;
  for (let attempt = 0; attempt < 80; attempt++) {
    const state = await (await fetch(`${origin}/preview/state`, { headers })).json();
    replied = state.observed.some(
      (item) => item.method === 'sendMessage' && item.payload.text === 'Preview channel response.',
    );
    if (replied) break;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  assert(replied, 'The real worker did not deliver its result through the fake provider.');
  await page.getByRole('button', { name: 'Edit', exact: true }).click();
  await page.getByLabel('Enable this account').uncheck();
  await page.getByRole('button', { name: 'Save account' }).click();
  await page.getByText('off', { exact: true }).first().waitFor();
  await mkdir('artifacts/channels', { recursive: true });
  await page.screenshot({ path: 'artifacts/channels/admin-desktop.png', fullPage: true });
  await page.setViewportSize({ width: 412, height: 915 });
  await page.waitForFunction(() => {
    const nav = document.querySelector('aside');
    return nav && nav.getBoundingClientRect().right <= 0;
  });
  const layout = await page.evaluate(() => ({
    width: innerWidth,
    scrollWidth: document.documentElement.scrollWidth,
    heading: document.querySelector('h1').getBoundingClientRect().toJSON(),
  }));
  assert(layout.scrollWidth <= layout.width, JSON.stringify(layout));
  assert(layout.heading.x >= 0, JSON.stringify(layout));
  await page.getByRole('button', { name: 'Open navigation' }).click();
  await page.getByRole('link', { name: 'Channels', exact: true }).click();
  await page.waitForFunction(
    () => document.querySelector('aside').getBoundingClientRect().right <= 0,
  );
  await page.screenshot({ path: 'artifacts/channels/admin-mobile.png', fullPage: true });
  assert.deepEqual(errors, []);
  console.log(
    'PASS real Admin save/edit, secret clearing, owner intake, durable worker and reply, account disable; fake Telegram only.',
  );
} finally {
  await browser?.close();
  server.kill();
  if (server.exitCode === null) await new Promise((resolve) => server.once('exit', resolve));
}
