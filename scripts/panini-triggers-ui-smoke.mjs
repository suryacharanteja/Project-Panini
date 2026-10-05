import { spawn } from 'node:child_process';
import { mkdir } from 'node:fs/promises';
import { resolve } from 'node:path';
import { chromium } from 'playwright';

const port = 3246;
const server = spawn(
  process.execPath,
  [
    resolve('node_modules/vite/bin/vite.js'),
    '--host',
    '127.0.0.1',
    '--port',
    String(port),
    '--strictPort',
  ],
  { cwd: resolve('panini-core/apps/admin'), windowsHide: true, stdio: 'ignore' },
);
let browser;
try {
  let ready = false;
  for (let i = 0; i < 100; i++) {
    try {
      ready = (await fetch(`http://127.0.0.1:${port}/admin/build/automations`)).ok;
    } catch {}
    if (ready) break;
    await new Promise((done) => setTimeout(done, 100));
  }
  if (!ready) throw new Error('Admin preview did not start.');
  browser = await chromium.launch({ headless: true, channel: 'chrome' });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const errors = [];
  const writes = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.route(`http://127.0.0.1:${port}/build/automations`, async (route) => {
    const response = await page.request.get(`http://127.0.0.1:${port}/admin/`);
    await route.fulfill({ response });
  });
  await page.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (route.request().method() === 'POST')
      writes.push({ path, body: route.request().postDataJSON() });
    const data =
      path === '/api/harness/tasks'
        ? { tasks: [] }
        : path === '/api/harness/triggers'
          ? { enabled: true, needs_one_brain: false, rules: [], responsibilities: [], bindings: [] }
          : path === '/api/admin/v1/personas'
            ? { personas: [{ id: 'panini', name: 'PANINI' }] }
            : {};
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(data),
    });
  });
  await page.goto(`http://127.0.0.1:${port}/build/automations`);
  await page.getByText('Event rules', { exact: true }).waitFor();
  await page.getByLabel('Rule name').fill('Invoice review');
  await page.getByLabel('Watched folder').fill('C:\\Users\\Owner\\Downloads');
  await page.getByLabel('Absolute file pattern').fill('C:\\Users\\Owner\\Downloads\\*invoice*.pdf');
  await page.getByLabel('What should PANINI do?').fill('Summarize the new invoice');
  await page.getByRole('button', { name: 'Propose rule' }).click();
  await page.waitForFunction(() => document.body.textContent?.includes('Rule proposed'));
  const proposed = writes.find((write) => write.path === '/api/harness/triggers');
  if (!proposed || proposed.body.match.watch_root !== 'C:\\Users\\Owner\\Downloads')
    throw new Error('The reviewed rule form submitted the wrong folder.');
  await page.getByLabel('Start from an example').selectOption('meeting');
  await page.getByRole('button', { name: 'Propose responsibility' }).click();
  await page.waitForFunction(() => document.body.textContent?.includes('Responsibility proposed'));
  const responsibility = writes.find(
    (write) => write.path === '/api/harness/triggers/responsibilities',
  );
  if (
    !responsibility ||
    responsibility.body.charter !== 'Review my upcoming meetings and relevant context.'
  )
    throw new Error('The responsibility template did not populate its charter.');
  await mkdir('artifacts/triggers', { recursive: true });
  await page.screenshot({ path: 'artifacts/triggers/admin-desktop.png', fullPage: true });
  const mobile = await browser.newPage({ viewport: { width: 390, height: 844 } });
  mobile.on('pageerror', (error) => errors.push(error.message));
  await mobile.route(`http://127.0.0.1:${port}/build/automations`, async (route) => {
    const response = await mobile.request.get(`http://127.0.0.1:${port}/admin/`);
    await route.fulfill({ response });
  });
  await mobile.route('**/api/**', (route) => {
    const path = new URL(route.request().url()).pathname;
    const data =
      path === '/api/harness/tasks'
        ? { tasks: [] }
        : path === '/api/harness/triggers'
          ? { enabled: true, needs_one_brain: false, rules: [], responsibilities: [], bindings: [] }
          : path === '/api/admin/v1/personas'
            ? { personas: [{ id: 'panini', name: 'PANINI' }] }
            : {};
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(data),
    });
  });
  await mobile.goto(`http://127.0.0.1:${port}/build/automations`);
  await mobile.getByText('Event rules', { exact: true }).waitFor();
  await mobile.screenshot({ path: 'artifacts/triggers/admin-mobile.png', fullPage: true });
  if (errors.length) throw new Error(errors.join('\n'));
  console.log(
    'PASS Admin rule and responsibility forms, desktop and mobile viewport; mocked API preview.',
  );
} finally {
  await browser?.close();
  server.kill();
}
