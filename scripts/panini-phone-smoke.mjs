import { spawn } from 'node:child_process';
import { chromium } from 'playwright';
import { mkdir, readFile } from 'node:fs/promises';
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
let diagnostic = '';
server.stderr.on('data', (chunk) => {
  diagnostic = (diagnostic + chunk).slice(-5000);
});
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
  const originalConversation = await control('/conversation', 'POST');
  await page.getByRole('button', { name: 'Work', exact: true }).click();
  await page.getByRole('button', { name: 'Conversations · continue on PC', exact: true }).click();
  await page.getByRole('button', { name: 'Preview continuity conversation', exact: true }).click();
  await page.getByText('USER: Continue the original conversation', { exact: true }).waitFor();
  await page.getByRole('button', { name: 'Continue on my PC', exact: true }).click();
  const continuity = await control('/continuity');
  if (
    !continuity.commands.some(
      (command) =>
        command.kind === 'handoff' && command.data.thread_id === originalConversation.thread_id,
    )
  )
    throw Error('Handoff created a replacement conversation');
  if (!continuity.signals.some((signal) => signal.source.startsWith('phone:') && signal.fresh))
    throw Error('Encrypted phone foreground heartbeat missing');
  await page.screenshot({ path: 'artifacts/phone/android-continuity.png' });
  await control('/interrupt', 'POST');
  await page.getByRole('button', { name: 'Inbox', exact: true }).click();
  await page.getByRole('button', { name: 'Approve', exact: true }).click();
  await page.getByText('Nothing needs you right now.', { exact: true }).waitFor();
  const result = await control('/state');
  if (result.interrupts[0].status !== 'resolved') throw Error('Approval did not resolve');
  await control('/mcp-interrupt', 'POST');
  await page.getByRole('button', { name: 'Inbox', exact: true }).click();
  await page.getByRole('button', { name: 'Allow this tool for 1 hour', exact: true }).waitFor();
  await page.screenshot({ path: 'artifacts/phone/android-mcp-approval.png' });
  await page.getByRole('button', { name: 'Allow this tool for 1 hour', exact: true }).click();
  await page.getByText('Nothing needs you right now.', { exact: true }).waitFor();
  const granted = await control('/state');
  if (
    !granted.interrupts.some(
      (item) =>
        item.reason === 'mcp_effect' &&
        item.status === 'resolved' &&
        item.decision?.payload?.allow_for_session === true,
    )
  )
    throw Error('Signed temporary permission did not resolve');
  const authenticator = await context.newCDPSession(page);
  await authenticator.send('WebAuthn.enable');
  await authenticator.send('WebAuthn.addVirtualAuthenticator', {
    options: {
      protocol: 'ctap2',
      transport: 'internal',
      hasResidentKey: true,
      hasUserVerification: true,
      isUserVerified: true,
      automaticPresenceSimulation: true,
    },
  });
  await page.getByRole('button', { name: 'Settings', exact: true }).click();
  await page.getByRole('button', { name: 'Add passkey', exact: true }).click();
  await page.getByRole('button', { name: 'Remove passkey', exact: true }).waitFor();
  await page.screenshot({ path: 'artifacts/phone/android-passkeys.png' });
  await page.getByRole('button', { name: 'Open notebook', exact: true }).click();
  page.once('dialog', (dialog) => dialog.accept('Phone notebook'));
  await page.getByRole('button', { name: 'New space', exact: true }).click();
  await page.getByLabel('Notebook space').selectOption({ label: 'Phone notebook' });
  page.once('dialog', (dialog) => dialog.accept('Mobile plan'));
  await page.getByRole('button', { name: 'New page', exact: true }).click();
  await page
    .getByLabel('Notebook Markdown')
    .fill('# Mobile mission\n\nSaved through the encrypted fabric.');
  await page.getByRole('button', { name: 'Save page', exact: true }).click();
  await page.getByText('Saved revision 2.', { exact: true }).waitFor();
  await page.getByLabel('Notebook Markdown').fill('Unsent draft retained');
  await page.getByRole('button', { name: 'Settings', exact: true }).click();
  await page.getByRole('button', { name: 'Open notebook', exact: true }).click();
  await page.getByLabel('Notebook space').selectOption({ label: 'Phone notebook' });
  await page.getByRole('button', { name: 'Mobile plan', exact: true }).click();
  if ((await page.getByLabel('Notebook Markdown').inputValue()) !== 'Unsent draft retained')
    throw Error('Phone notebook draft was lost');
  await page.getByRole('button', { name: 'Save page', exact: true }).click();
  await page.getByText('Saved revision 3.', { exact: true }).waitFor();
  await control('/notebook/concurrent-edit', 'POST');
  await page.getByLabel('Notebook Markdown').fill('Owner merged phone draft');
  await page.getByRole('button', { name: 'Save page', exact: true }).click();
  await page.getByText(/Your draft is kept. Reopen the page to compare./).waitFor();
  await page.getByRole('button', { name: 'Mobile plan', exact: true }).click();
  await page.getByText('Compare server revision', { exact: true }).click();
  await page.getByText('Concurrent PC edit', { exact: true }).waitFor();
  page.once('dialog', (dialog) => dialog.accept());
  await page.getByRole('button', { name: 'Use current revision', exact: true }).click();
  await page.getByRole('button', { name: 'Save page', exact: true }).click();
  await page.getByText('Saved revision 5.', { exact: true }).waitFor();
  if (await page.getByText('Compare server revision', { exact: true }).count())
    throw Error('Resolved notebook conflict remains actionable');
  let download = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Export Markdown', exact: true }).click();
  if (!(await readFile(await (await download).path(), 'utf8')).includes('Owner merged phone draft'))
    throw Error('Phone Markdown export did not retain saved content');
  download = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Export PDF', exact: true }).click();
  if (!(await readFile(await (await download).path())).subarray(0, 5).equals(Buffer.from('%PDF-')))
    throw Error('Phone PDF export was not a PDF');
  await page.screenshot({ path: 'artifacts/phone/android-notebook.png' });
  const verifiedCard = await control('/interrupt?level=verify', 'POST');
  await page.getByRole('button', { name: 'Inbox', exact: true }).click();
  await page.getByText('Confirm this exact decision with your passkey.', { exact: true }).waitFor();
  await page.getByRole('button', { name: 'Approve', exact: true }).click();
  await page.getByText('Nothing needs you right now.', { exact: true }).waitFor();
  if (
    (await control('/state')).interrupts.find((i) => i.id === verifiedCard.id)?.decided_by
      ?.method !== 'passkey'
  )
    throw Error('Passkey approval did not resolve with verified authority');
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
  await control('/call', 'POST');
  await page.getByRole('button', { name: 'Home', exact: true }).click();
  await page.getByRole('button', { name: 'Phone calls', exact: true }).click();
  await page.getByText('Waiting for keypad PIN or phone confirmation', { exact: true }).waitFor();
  await page.getByRole('button', { name: 'Verify this is me', exact: true }).click();
  await page.getByText('Verify phone caller', { exact: true }).waitFor();
  await page.getByRole('button', { name: 'Approve', exact: true }).click();
  await page.getByRole('button', { name: 'Home', exact: true }).click();
  await page.getByRole('button', { name: 'Phone calls', exact: true }).click();
  await page.getByText('Caller verified', { exact: true }).waitFor();
  await page.screenshot({ path: 'artifacts/phone/android-call.png' });
  await page.getByRole('button', { name: 'End call', exact: true }).click();
  await page.getByText('No active call', { exact: true }).waitFor();
  await page.getByText(/Phone call ended after/).waitFor();
  await page.getByRole('button', { name: 'Save to Space', exact: true }).click();
  await page.getByLabel('Save destination').selectOption({ label: 'Phone notebook' });
  await page.getByRole('button', { name: 'Save source', exact: true }).click();
  await page.getByText('Saved to your notebook.', { exact: true }).waitFor();
  await page.screenshot({ path: 'artifacts/phone/android-call-receipt.png' });
  await page.getByRole('button', { name: 'Inbox', exact: true }).click();
  await page.getByText('Phone call receipt', { exact: true }).waitFor();
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
  await page.goto(`http://localhost:${phonePort}/?tab=ask`);
  await page.getByRole('heading', { name: 'Shared with PANINI', exact: true }).waitFor();
  await page.getByRole('button', { name: 'Send', exact: true }).click();
  await page.getByRole('heading', { name: 'Ready', exact: true }).waitFor();
  if ((await control('/state')).asks.length !== 2) throw Error('Share did not arrive');
  // V2-15: continue an owner conversation from the phone, with a photo attached.
  await page.goto(`http://localhost:${phonePort}/`);
  await page.getByRole('button', { name: 'Work', exact: true }).click();
  await page.getByRole('button', { name: 'Conversations · continue on PC', exact: true }).click();
  await page.getByRole('button', { name: 'Preview continuity conversation', exact: true }).click();
  await page.getByRole('button', { name: 'Continue here', exact: true }).click();
  await page.getByText('Continuing', { exact: true }).waitFor();
  await page.getByRole('button', { name: 'Dictate', exact: true }).waitFor();
  const png = Buffer.from(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==',
    'base64',
  );
  await page.locator('input[type=file]').setInputFiles({ name: 'receipt.png', mimeType: 'image/png', buffer: png });
  await page.getByText(/receipt\.png \(1 KB\)/).waitFor();
  await page.getByRole('textbox', { name: 'Ask PANINI', exact: true }).fill('Add this receipt to the trip.');
  await page.getByRole('button', { name: 'Send', exact: true }).click();
  await page.getByRole('heading', { name: 'Ready', exact: true }).waitFor();
  const continued = await control('/state');
  const last = continued.asks.at(-1);
  if (!last.goal.includes('Add this receipt') || !last.goal.includes('<untrusted source="shared">'))
    throw Error('Attachment did not reach the PC as untrusted content: ' + last.goal.slice(0, 400));
  if (!continued.task_threads.some((row) => row.thread_id === originalConversation.thread_id))
    throw Error('The phone turn did not continue the original conversation');
  await page.screenshot({ path: 'artifacts/phone/android-continue-attach.png' });
  await page.goto(`http://localhost:${phonePort}/`);
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
    'PASS encrypted pairing, persistent non-extractable keys, signed tap approval, real WebAuthn enrollment and verify approval with virtual authenticator, phone notebook create/edit/draft recovery and saved call receipt, Ask exactly once, continue a conversation with a photo attached, task history/stop, phone call verification/end/receipt/Inbox delivery, reload, Live, Android share target, offline recovery and revocation in Android-sized Chromium',
  );
} catch (error) {
  for (const page of browser?.contexts().flatMap((c) => c.pages()) || []) {
    console.error((await page.locator('main').innerText()).slice(0, 1200));
  }
  console.error(diagnostic);
  throw error;
} finally {
  await browser?.close();
  server.kill();
}
