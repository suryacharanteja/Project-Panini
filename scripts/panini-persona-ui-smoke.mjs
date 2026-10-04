import { spawn } from 'node:child_process';
import { mkdir } from 'node:fs/promises';
import { chromium } from 'playwright';

const server = spawn(
  process.execPath,
  [
    'node_modules/vite/bin/vite.js',
    'panini-core/apps/shell/test/gallery',
    '--host',
    '127.0.0.1',
    '--port',
    '3233',
    '--strictPort',
  ],
  { windowsHide: true, stdio: 'ignore' },
);
let browser;
try {
  let ready = false;
  for (let i = 0; i < 80; i++) {
    try {
      ready = (await fetch('http://127.0.0.1:3233/shell.html')).ok;
    } catch {}
    if (ready) break;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  if (!ready) throw new Error('Avatar preview did not start.');
  browser = await chromium.launch({ headless: true, channel: 'chrome' });
  const page = await browser.newPage({ viewport: { width: 1440, height: 980 } });
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.goto('http://127.0.0.1:3233/shell.html?personas');
  await page.locator('.brand strong').getByText('PANINI', { exact: true }).waitFor();
  await page.evaluate(() =>
    window.emitPersonaPreview(
      { id: 'scout', name: 'Scout', color: 'cyan' },
      'Research sources checked.',
    ),
  );
  await page.locator('.brand strong').getByText('Scout', { exact: true }).waitFor();
  await page.locator('canvas[data-persona-color="cyan"]').first().waitFor();
  await page.locator('.persona-timeline summary').click();
  await page
    .locator('.persona-chip[data-color="cyan"]')
    .getByText('Scout', { exact: true })
    .waitFor();
  await mkdir('artifacts/personas', { recursive: true });
  await page.screenshot({ path: 'artifacts/personas/avatar-scout.png' });
  await page.evaluate(() =>
    window.emitPersonaPreview(
      { id: 'quill', name: 'Quill', color: 'violet' },
      '<img src=x onerror=alert(1)> Writing complete.',
    ),
  );
  await page.locator('.brand strong').getByText('Quill', { exact: true }).waitFor();
  await page.locator('canvas[data-persona-color="violet"]').first().waitFor();
  if (await page.locator('.persona-timeline img').count())
    throw new Error('A message inserted HTML.');
  await page.screenshot({ path: 'artifacts/personas/avatar-quill.png' });
  await page.getByRole('button', { name: 'Full screen (F11)', exact: true }).click();
  await page.evaluate(() => {
    const host = document.getElementById('root').parentElement;
    host.style.width = '100vw';
    host.style.height = '100vh';
    host.style.margin = '0';
  });
  await page.locator('.engine .panel-title').getByText('Quill', { exact: false }).waitFor();
  await page.screenshot({ path: 'artifacts/personas/avatar-quill-stage.png' });
  await page.evaluate(() =>
    window.emitPersonaPreview(
      { id: 'panini', name: 'PANINI', color: 'copper' },
      'Here is the team summary.',
    ),
  );
  await page.locator('.brand strong').getByText('PANINI', { exact: true }).waitFor();
  await page.locator('canvas[data-persona-color="copper"]').first().waitFor();
  if (errors.length) throw new Error(errors.join('\n'));
  console.log(
    'PASS avatar identity switching, colour chips, attributed text escaping and full stage; scripted local preview.',
  );
} finally {
  await browser?.close();
  server.kill();
}
