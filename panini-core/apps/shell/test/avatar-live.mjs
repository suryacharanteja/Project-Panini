// Live check of the avatar in the real app: it listens to a real (synthetic) microphone
// over the Live API, and the opt-in full-screen delegation overlay opens click-through.
import { _electron as electron } from 'playwright';
import { createRequire } from 'node:module';
import { mkdir } from 'node:fs/promises';
import { resolve } from 'node:path';

const require = createRequire(import.meta.url);
const root = resolve(import.meta.dirname, '../..');
const output = resolve(root, 'artifacts/panini-smoke/avatar');
await mkdir(output, { recursive: true });
process.loadEnvFile(resolve(root, '.env'));
const env = { ...process.env, PANINI_NODE: process.execPath };
delete env.ELECTRON_RUN_AS_NODE;
const application = await electron.launch({
  executablePath: require('electron'),
  args: [
    resolve(root, 'panini-core/apps/shell'),
    '--use-fake-ui-for-media-stream',
    '--use-fake-device-for-media-stream',
  ],
  env,
  timeout: 60000,
});
const checks = [];
const avatarState = (page) => page.locator('.presence canvas').getAttribute('data-avatar-state');
try {
  const page = await application.firstWindow();
  for (let i = 0; i < 150; i++) {
    const s = await page.evaluate(() => window.panini.invoke('status'));
    if (s.connected && s.snapshot?.state === 'ready') break;
    await page.waitForTimeout(500);
  }
  if ((await avatarState(page)) !== 'idle')
    throw new Error(`Expected idle, got ${await avatarState(page)}`);
  checks.push('idle when ready');

  // Listening: a real WebRTC voice session with Chromium's synthetic microphone (a tone).
  await page.getByRole('button', { name: 'Talk to PANINI' }).click();
  await page.waitForFunction(
    () => document.querySelector('.mic.active') || document.querySelector('.error'),
    null,
    {
      timeout: 55000,
    },
  );
  if (!(await page.getByRole('button', { name: 'Mic on' }).count()))
    throw new Error('Voice did not connect: ' + (await page.locator('.error').innerText()));
  await page.waitForTimeout(1200);
  if ((await avatarState(page)) !== 'listening')
    throw new Error(`Expected listening, got ${await avatarState(page)}`);
  await page.screenshot({ path: resolve(output, 'listening.png') });
  checks.push('listening while the live microphone is on');
  await page.getByRole('button', { name: 'Mute', exact: true }).click();
  await page.waitForTimeout(400);
  if ((await avatarState(page)) !== 'muted')
    throw new Error(`Expected muted, got ${await avatarState(page)}`);
  checks.push('muted when the microphone is muted');
  await page.getByRole('button', { name: 'Mic on' }).click();
  await page.waitForTimeout(800);

  // The full-screen delegation moment: opt-in, click-through, never focused, then gone.
  const before = await page.evaluate(() => window.panini.invoke('status'));
  const original = !!before.preferences.delegationOverlay;
  await page.evaluate(() =>
    window.panini.invoke('window', { action: 'delegation-overlay', value: true }),
  );
  const opened = application.waitForEvent('window', { timeout: 10000 });
  await page.evaluate(() =>
    window.panini.invoke('window', { action: 'delegate', value: 'search' }),
  );
  const overlay = await opened;
  await overlay.waitForTimeout(700);
  const props = await application.evaluate(({ BrowserWindow }) => {
    const w = BrowserWindow.getAllWindows().find((x) =>
      x.webContents.getURL().includes('overlay.html'),
    );
    return w && { focusable: w.isFocusable(), focused: w.isFocused(), onTop: w.isAlwaysOnTop() };
  });
  if (!props || props.focusable || props.focused || !props.onTop)
    throw new Error('Overlay: ' + JSON.stringify(props));
  await overlay.screenshot({ path: resolve(output, 'delegation.png') }).catch(() => {});
  checks.push('delegation overlay opens unfocusable and on top');
  await page.waitForTimeout(2600);
  const remaining = await application.evaluate(
    ({ BrowserWindow }) =>
      BrowserWindow.getAllWindows().filter(
        (x) => !x.isDestroyed() && x.webContents.getURL().includes('overlay.html'),
      ).length,
  );
  if (remaining) throw new Error('Overlay did not close');
  checks.push('delegation overlay closes itself');
  await page.evaluate(
    (value) => window.panini.invoke('window', { action: 'delegation-overlay', value }),
    original,
  );
  console.log(JSON.stringify({ checks, errors: [] }));
} finally {
  await application.close();
}

