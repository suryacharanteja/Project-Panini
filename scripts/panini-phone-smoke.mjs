import { spawn } from 'node:child_process';
import { chromium } from 'playwright';
import { mkdir } from 'node:fs/promises';
const phonePort = 3221,
  controlPort = 3222;
const server = spawn(
  '.venv/Scripts/python.exe',
  [
    'scripts/panini-phone-dev.py',
    '--phone-port',
    String(phonePort),
    '--control-port',
    String(controlPort),
    '--personas',
  ],
  { windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] },
);
let browser;
try {
  const link = await new Promise((resolve, reject) => {
    let output = '';
    const timer = setTimeout(() => reject(new Error('Preview startup timeout')), 20000);
    server.stdout.on('data', (chunk) => {
      output += chunk;
      const match = output.match(/Pairing link[^:]*:\s*(http[^\s]+)/);
      if (match) {
        clearTimeout(timer);
        resolve(match[1]);
      }
    });
    server.on('exit', (code) => {
      clearTimeout(timer);
      reject(new Error('Preview exited ' + code));
    });
  });
  const control = async (path, method = 'GET') => {
    const r = await fetch(`http://127.0.0.1:${controlPort}${path}`, { method });
    if (!r.ok) throw Error('Control failed');
    return r.json();
  };
  for (let i = 0; i < 40; i++) {
    try {
      await control('/state');
      break;
    } catch {
      await new Promise((r) => setTimeout(r, 100));
    }
  }
  browser = await chromium.launch({ headless: true, channel: 'chrome' });
  const context = await browser.newContext({
    viewport: { width: 412, height: 915 },
    isMobile: true,
    hasTouch: true,
  });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await page.goto(link);
  await page.getByRole('button', { name: 'Pair this phone', exact: true }).click();
  await page.locator('.sas').waitFor();
  const state = await control('/state');
  if (!(await page.locator('.sas').innerText())) throw Error('No pairing digits');
  await control('/confirm', 'POST');
  await page.getByRole('heading', { name: 'Ready', exact: true }).waitFor();
  const nonExtractable = await page.evaluate(async () => {
    const { get } = await import('/lib/db.mjs');
    const d = await get('device');
    return !d.sign.privateKey.extractable && !d.agree.privateKey.extractable;
  });
  if (!nonExtractable) throw Error('Keys extractable');
  await mkdir('artifacts/phone', { recursive: true });
  await page.screenshot({ path: 'artifacts/phone/android-home.png' });
  await control('/interrupt', 'POST');
  await page.getByRole('button', { name: 'Inbox', exact: true }).click();
  await page.getByRole('button', { name: 'Approve', exact: true }).click();
  await page.getByText('Nothing needs you right now.', { exact: true }).waitFor();
  const result = await control('/state');
  if (result.interrupts[0].status !== 'resolved') throw Error('Approval did not resolve');
  await page.getByRole('button', { name: 'Ask', exact: true }).click();
  await page.getByRole('combobox', { name: 'Work with' }).selectOption('scout');
  await page
    .getByRole('textbox', { name: 'Ask PANINI', exact: true })
    .fill('Prepare my Android companion status.');
  await page.getByRole('button', { name: 'Send', exact: true }).click();
  await page.getByRole('heading', { name: 'Ready', exact: true }).waitFor();
  if ((await control('/state')).asks.length !== 1) throw Error('Ask not received exactly once');
  if (!(await control('/state')).asks[0].goal.startsWith('@scout '))
    throw Error('Persona selection was lost');
  await page.getByRole('button', { name: 'Work', exact: true }).click();
  await page.getByRole('button', { name: /Prepare my Android companion status/ }).click();
  await page
    .locator('.persona-chip[data-color="cyan"]')
    .getByText('Scout', { exact: true })
    .waitFor();
  await page.getByText('Scripted preview: Scout received this request.', { exact: true }).waitFor();
  for (const colorScheme of ['light', 'dark']) {
    await page.emulateMedia({ colorScheme });
    const contrast = await page.evaluate(() => {
      const luminance = (color) => {
        const rgb = color
          .match(/[\d.]+/g)
          .slice(0, 3)
          .map((n) => Number(n) / 255)
          .map((v) => (v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
        return rgb[0] * 0.2126 + rgb[1] * 0.7152 + rgb[2] * 0.0722;
      };
      const chip = document.querySelector('.persona-chip');
      const bg = luminance(getComputedStyle(document.body).backgroundColor);
      const ratios = ['copper', 'cyan', 'violet', 'green', 'amber', 'rose'].map((token) => {
        chip.dataset.color = token;
        const fg = luminance(getComputedStyle(chip).color);
        return (Math.max(fg, bg) + 0.05) / (Math.min(fg, bg) + 0.05);
      });
      chip.dataset.color = 'cyan';
      return Math.min(...ratios);
    });
    if (contrast < 4.5) throw Error(`${colorScheme} persona contrast is ${contrast}`);
  }
  await page.emulateMedia({ colorScheme: 'light' });
  await page.getByRole('button', { name: 'Stop task', exact: true }).click();
  await page.getByText('cancelled', { exact: true }).waitFor();
  await page.screenshot({ path: 'artifacts/phone/android-work.png' });
  await page.getByRole('button', { name: 'Home', exact: true }).click();
  await page.getByRole('heading', { name: 'Ready', exact: true }).waitFor();
  await page.reload();
  await page.getByRole('heading', { name: 'Ready', exact: true }).waitFor();
  await page.getByRole('button', { name: 'Live', exact: true }).click();
  await page.locator('img.frame').waitFor();
  await page.screenshot({ path: 'artifacts/phone/android-live.png' });
  await page.evaluate(async () => {
    await navigator.serviceWorker.ready;
    if (!navigator.serviceWorker.controller)
      await new Promise((resolve) =>
        navigator.serviceWorker.addEventListener('controllerchange', resolve, { once: true }),
      );
    const form = new FormData();
    form.set('title', 'Android share test');
    form.set('url', 'https://example.com/panini');
    const response = await fetch('/share', { method: 'POST', body: form });
    if (!response.ok || !response.url.includes('tab=ask'))
      throw new Error('Share target redirect failed');
  });
  await page.goto(`http://127.0.0.1:${phonePort}/?tab=ask`);
  await page.getByRole('heading', { name: 'Shared with PANINI', exact: true }).waitFor();
  await page.getByRole('button', { name: 'Send', exact: true }).click();
  await page.getByRole('heading', { name: 'Ready', exact: true }).waitFor();
  if ((await control('/state')).asks.length !== 2) throw Error('Share did not arrive');
  await page.goto(`http://127.0.0.1:${phonePort}/`);
  await page.getByRole('heading', { name: 'Ready', exact: true }).waitFor();
  await context.setOffline(true);
  await page.reload();
  await page
    .getByRole('heading', { name: 'Your PC is offline', exact: true })
    .waitFor({ timeout: 45000 })
    .catch(async (e) => {
      console.log(await page.locator('main').innerText());
      throw e;
    });
  await context.setOffline(false);
  await page.getByRole('button', { name: 'Try again', exact: true }).click();
  await page.getByRole('heading', { name: 'Ready', exact: true }).waitFor();
  await control('/revoke', 'POST');
  await page.reload();
  await page
    .getByRole('heading', { name: 'This phone was removed from PANINI', exact: true })
    .waitFor();
  if (
    !(await page.evaluate(async () => {
      const { get } = await import('/lib/db.mjs');
      return !(await get('device'));
    }))
  )
    throw Error('Revoked keys retained');
  if (errors.length) throw Error(errors.join('\n'));
  console.log(
    'PASS encrypted pairing, persistent non-extractable keys, signed approval, Ask exactly once, task history/stop, reload, Live, Android share target, offline recovery and revocation in Android-sized Chromium',
  );
} finally {
  await browser?.close();
  server.kill();
}
