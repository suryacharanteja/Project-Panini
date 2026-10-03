import { spawn } from 'node:child_process';
import { randomBytes } from 'node:crypto';
import { mkdir } from 'node:fs/promises';
import { chromium } from 'playwright';

const port = 3231;
const token = randomBytes(32).toString('hex');
const server = spawn('.venv/Scripts/python.exe', ['scripts/panini-persona-dev.py'], {
  windowsHide: true,
  stdio: ['ignore', 'pipe', 'pipe'],
  env: {
    ...process.env,
    PANINI_PERSONA_PREVIEW_TOKEN: token,
    PANINI_PERSONA_PREVIEW_PORT: String(port),
  },
});
let browser;
let diagnostic = '';
server.stderr.on('data', (chunk) => {
  diagnostic += chunk;
});
try {
  for (let attempt = 0; attempt < 50; attempt++) {
    const ready = await fetch(`http://127.0.0.1:${port}/api/admin/v1/personas`, {
      headers: { 'x-panini-console': token },
    }).catch(() => null);
    if (ready?.ok) break;
    if (server.exitCode !== null) throw new Error(`Preview exited: ${diagnostic}`);
    await new Promise((resolve) => setTimeout(resolve, 200));
    if (attempt === 49) throw new Error('Persona preview did not become ready.');
  }
  browser = await chromium.launch({ headless: true, channel: 'chrome' });
  const context = await browser.newContext({
    viewport: { width: 1440, height: 1000 },
    extraHTTPHeaders: { 'x-panini-console': token },
  });
  const page = await context.newPage();
  page.setDefaultTimeout(10000);
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.goto(`http://127.0.0.1:${port}/build/personas`);
  await page.getByRole('button', { name: 'New persona', exact: true }).click();
  await page.getByLabel('ID', { exact: true }).fill('scout');
  await page.getByLabel('Name', { exact: true }).fill('Scout');
  await page.getByLabel('Role', { exact: true }).fill('Research specialist');
  await page.getByLabel('Instructions').fill('Research carefully and cite sources.');
  await page.getByRole('button', { name: 'Save', exact: true }).click();
  const card = page
    .locator('div.rounded-lg')
    .filter({ has: page.getByRole('heading', { name: 'Scout @scout' }) });
  await card.getByRole('button', { name: 'Edit', exact: true }).click();
  await page.getByLabel('memory read', { exact: true }).check();
  await page.getByLabel('memory write', { exact: true }).check();
  await page.getByLabel('Allow memory writes within this scope').check();
  await page.getByRole('button', { name: 'Save', exact: true }).click();
  await card.getByRole('button', { name: 'Test identity' }).click();
  await card.getByText('I am your specialist, ready for an assigned task.').waitFor();
  await page.reload();
  await card.getByRole('button', { name: 'Edit', exact: true }).click();
  if (!(await page.getByLabel('memory read', { exact: true }).isChecked()))
    throw new Error('Permission did not persist.');
  await mkdir('artifacts/personas', { recursive: true });
  await page.screenshot({ path: 'artifacts/personas/admin-desktop.png', fullPage: true });
  await page.setViewportSize({ width: 412, height: 915 });
  await page.waitForFunction(
    () => document.querySelector('aside').getBoundingClientRect().right <= 1,
  );
  await page.screenshot({ path: 'artifacts/personas/admin-mobile.png', fullPage: true });
  if (await page.evaluate(() => document.documentElement.scrollWidth > innerWidth))
    throw new Error('Persona editor overflows the phone viewport.');
  await page.getByRole('button', { name: 'Cancel', exact: true }).click();
  await card.getByRole('button', { name: 'Archive', exact: true }).click();
  await card.waitFor({ state: 'detached' });
  if (errors.length) throw new Error(errors.join('\n'));
  console.log(
    'Persona Admin smoke passed: create, edit, grants, identity test, reload, mobile layout, archive.',
  );
} finally {
  await browser?.close();
  server.kill();
}
