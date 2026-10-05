import { spawn } from 'node:child_process';
import { mkdir } from 'node:fs/promises';
import { resolve } from 'node:path';
import { chromium } from 'playwright';

const port = 3249;
const base = `http://127.0.0.1:${port}`;
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
      ready = (await fetch(`${base}/admin/`)).ok;
    } catch {}
    if (ready) break;
    await new Promise((done) => setTimeout(done, 100));
  }
  if (!ready) throw new Error('Admin preview did not start.');
  browser = await chromium.launch({ headless: true, channel: 'chrome' });
  const errors = [];
  await mkdir('artifacts/local', { recursive: true });
  for (const [label, width, height] of [
    ['desktop', 1440, 1000],
    ['mobile', 390, 844],
  ]) {
    const page = await browser.newPage({ viewport: { width, height } });
    page.on('pageerror', (error) => errors.push(error.message));
    await page.route(`${base}/settings/keys`, async (route) => {
      const response = await page.request.get(`${base}/admin/`);
      await route.fulfill({ response });
    });
    let probed = false;
    await page.route('**/api/**', async (route) => {
      const path = new URL(route.request().url()).pathname;
      if (path === '/api/admin/v1/local-model/test') {
        probed = true;
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ ok: true, detail: 'small-local answered the tool-call test.' }),
        });
      }
      const data =
        path === '/api/admin/v1/settings'
          ? {
              groups: { keys: 'Keys', models: 'Models' },
              settings: [
                {
                  key: 'PANINI_LOCAL_BASE_URL',
                  group: 'models',
                  label: 'Local model server',
                  type: 'text',
                  value: 'http://127.0.0.1:11434/v1',
                },
                {
                  key: 'PANINI_LOCAL_MODEL',
                  group: 'models',
                  label: 'Local model name',
                  type: 'text',
                  value: 'small-local',
                },
              ],
              restart_needed: [],
            }
          : {};
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(data),
      });
    });
    await page.goto(`${base}/settings/keys`);
    await page.getByText('Local model preview', { exact: true }).waitFor();
    await page.getByRole('button', { name: 'Test local tool calling' }).click();
    await page.getByRole('status').getByText('small-local answered the tool-call test.').waitFor();
    if (!probed) throw new Error('The local model test request was not sent.');
    await page.waitForTimeout(450); // Let the settings card entrance animation settle before visual QA.
    await page.screenshot({ path: `artifacts/local/admin-${label}.png`, fullPage: true });
    await page.close();
  }
  if (errors.length) throw new Error(errors.join('\n'));
  console.log('PASS local model settings and probe UI at desktop and mobile widths.');
} finally {
  await browser?.close();
  server.kill();
}
