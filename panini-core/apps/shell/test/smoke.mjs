import { _electron as electron } from 'playwright';
import { createRequire } from 'node:module';
import { mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { execFileSync, spawn } from 'node:child_process';
const require = createRequire(import.meta.url);
const root = resolve(import.meta.dirname, '../../../..');
const profile =
  process.argv
    .slice(2)
    .map((value) => value.replaceAll('--', ''))
    .join('-') || 'shell';
const output = resolve(root, 'artifacts/panini-smoke', profile);
await mkdir(output, { recursive: true });
if (process.argv.includes('--configured')) process.loadEnvFile(resolve(root, '.env'));
const env = { ...process.env, PANINI_NODE: process.execPath };
delete env.ELECTRON_RUN_AS_NODE;
const packaged = process.argv.includes('--packaged');
const voiceArgs = process.argv.includes('--voice')
  ? ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream']
  : [];
const application = await electron.launch({
  executablePath: packaged
    ? resolve(root, 'artifacts/panini-package/win-unpacked/PANINI.exe')
    : require('electron'),
  args: [
    ...(packaged ? [] : [resolve(root, 'panini-core/apps/shell')]),
    ...voiceArgs,
    // An isolated profile (its own single-instance lock), so a test can run beside the owner's PANINI.
    ...(process.env.PANINI_SMOKE_USER_DATA
      ? [`--user-data-dir=${process.env.PANINI_SMOKE_USER_DATA}`]
      : []),
  ],
  env,
  timeout: 60000,
});
const checks = [],
  errors = [];
let fixture;
let viewer;
let calculatorPid;
let calculatorCreated;
function calculatorProcesses() {
  const raw = execFileSync(
    'powershell.exe',
    [
      '-NoProfile',
      '-Command',
      '@(Get-Process CalculatorApp,Calculator -ErrorAction SilentlyContinue | Select-Object Id,StartTime) | ConvertTo-Json -Compress',
    ],
    { windowsHide: true, encoding: 'utf8' },
  ).trim();
  const values = raw ? JSON.parse(raw) : [];
  return Array.isArray(values) ? values : [values];
}
try {
  const page = await application.firstWindow();
  await page.evaluate(() => {
    window.paniniTestEvents = [];
    window.panini.subscribe((event) => {
      window.paniniTestEvents.push(event);
    });
  });
  page.on('pageerror', (error) => errors.push(error.message));
  await page.getByRole('heading', { name: 'Let’s get something done.' }).waitFor();
  let current;
  for (let attempt = 0; attempt < 150; attempt++) {
    current = await page.evaluate(() => window.panini.invoke('status'));
    if (current.connected && current.snapshot?.state === 'ready') break;
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  if (!current?.snapshot)
    throw new Error('Runtime did not become ready: ' + (await page.locator('main').innerText()));
  checks.push('runtime startup and controller lease');
  const state = await page.evaluate(() => window.panini.invoke('status'));
  if (state.snapshot.state !== 'ready') throw new Error('Conversation is not ready');
  const caps = await page.evaluate(() => window.panini.invoke('capabilities'));
  checks.push(...caps.capabilities.map((cap) => `${cap.capability}: ${cap.state}`));
  await page.getByLabel('Conversation settings', { exact: true }).click();
  await page.getByText('Capability readiness', { exact: true }).click();
  for (const capability of caps.capabilities) {
    const label = capability.capability[0].toUpperCase() + capability.capability.slice(1);
    await page
      .getByText(`${label} · ${capability.state === 'ready' ? 'Ready' : 'Needs setup'}`, {
        exact: true,
      })
      .waitFor();
    if (capability.reason) await page.getByText(capability.reason, { exact: true }).waitFor();
  }
  checks.push('capability readiness and actionable setup explanations');
  await page.getByLabel('Conversation settings', { exact: true }).click();
  if (process.argv.includes('--observer') || process.argv.includes('--crash')) {
    await application.evaluate(({ shell }) => {
      shell.openExternal = async (url) => {
        globalThis.paniniTestViewerUrl = url;
      };
    });
    await page.evaluate(() => window.panini.invoke('dashboard', { shareCaptions: false }));
    const opened = application.waitForEvent('window');
    await application.evaluate(async ({ BrowserWindow }) => {
      const window = new BrowserWindow({
        width: 1200,
        height: 950,
        show: true,
        webPreferences: { nodeIntegration: false, contextIsolation: true, sandbox: true },
      });
      await window.loadURL(globalThis.paniniTestViewerUrl);
    });
    viewer = await opened;
    await viewer.getByText('Connected to PANINI.', { exact: false }).waitFor({ timeout: 15000 });
    const blocked = await viewer.evaluate(async () => {
      const session = JSON.parse(sessionStorage.getItem('panini.activity'));
      return (
        await fetch(`/api/panini/sessions/${session.sessionId}/stop`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ command_id: crypto.randomUUID() }),
        })
      ).status;
    });
    if (blocked !== 403) throw new Error('Observer could mutate the session');
    await viewer.screenshot({ path: resolve(output, 'observer.png') });
    checks.push('live observer ticket exchange, read-only dashboard and mutation denial');
  }
  await page
    .getByRole('textbox', { name: 'Your instruction' })
    .fill('Find official documentation about Windows UI Automation');
  await page.screenshot({ path: resolve(output, 'conversation.png') });
  await page.getByRole('button', { name: 'Collapse PANINI' }).click();
  await page.waitForTimeout(600); // Let the window settle into the capsule.
  await page.screenshot({ path: resolve(output, 'capsule.png') });
  await page.getByRole('button', { name: /^PANINI:.*Press Enter to open\.$/ }).click();
  checks.push('capsule, conversation and typed input');
  await page.evaluate(() => window.panini.invoke('stop'));
  checks.push('idle Stop');
  const capabilities = await page.evaluate(() =>
    window.panini.invoke('targets', { capability: 'browser' }),
  );
  if (!capabilities.targets.some((target) => target.choice_id === 'managed-chromium'))
    throw new Error('Managed browser target missing');
  checks.push('browser target selection available');
  if (process.argv.includes('--search')) {
    await page.evaluate(() =>
      window.panini.invoke('turn', {
        capability: 'search',
        text: "Find Microsoft's official Windows UI Automation overview documentation",
      }),
    );
    let result;
    let value;
    for (let attempt = 0; attempt < 200; attempt++) {
      value = await page.evaluate(() => window.panini.invoke('status'));
      result = value.snapshot?.task;
      if (['completed', 'blocked', 'failed', 'stopped'].includes(result?.state)) break;
      await new Promise((resolve) => setTimeout(resolve, 500));
    }
    if (
      result?.outcome !== 'verified_complete' ||
      !value.snapshot.results.some((result) => /^https?:\/\//.test(result.url))
    )
      throw new Error('Search workflow: ' + JSON.stringify(result));
    checks.push('real Search retrieval, Jev ranking and source-backed result records');
  }
  if (process.argv.includes('--voice')) {
    await page.getByRole('button', { name: 'Talk to PANINI' }).click();
    await page.waitForFunction(
      () => document.querySelector('.mic.active') || document.querySelector('.error'),
      null,
      { timeout: 55000 },
    );
    if (!(await page.getByRole('button', { name: 'Mic on' }).count()))
      throw new Error('Voice connection: ' + (await page.locator('.error').innerText()));
    const active = await page.evaluate(() => window.panini.invoke('status'));
    if (!active.snapshot.media_connected) throw new Error('Media session did not attach');
    await page.getByRole('button', { name: 'Mute', exact: true }).click();
    await page.getByRole('button', { name: 'Unmute', exact: true }).waitFor();
    await page.getByRole('button', { name: 'Mic on' }).click();
    for (let i = 0; i < 30; i++) {
      const state = await page.evaluate(() => window.panini.invoke('status'));
      if (!state.snapshot.media_connected) break;
      await new Promise((resolve) => setTimeout(resolve, 250));
    }
    const closed = await page.evaluate(() => window.panini.invoke('status'));
    if (closed.snapshot.media_connected) throw new Error('Media did not close');
    checks.push(
      'real Live API and WebRTC negotiation using synthetic microphone, local Mute and media teardown',
    );
  }
  if (process.argv.includes('--browser')) {
    await page.evaluate(() =>
      window.panini.invoke('select', { capability: 'browser', choice_id: 'managed-chromium' }),
    );
    await application.evaluate(({ dialog }) => {
      dialog.showMessageBox = async (_window, options) => ({
        response:
          ['Jev browser demo', '127.0.0.1:3200/browser-demo.html'].includes(options.message) ||
          options.detail?.includes('http://127.0.0.1:3200/browser-demo.html')
            ? 1
            : 0,
        checkboxChecked: false,
      });
    });
    await page.evaluate(() =>
      window.panini.invoke('turn', {
        capability: 'browser',
        text: 'Navigate to http://127.0.0.1:3200/browser-demo.html. Enter PANINI browser smoke in Your demo name, click Show greeting, and verify the greeting appears.',
      }),
    );
    let result;
    for (let attempt = 0; attempt < 600; attempt++) {
      const value = await page.evaluate(() => window.panini.invoke('status'));
      result = value.snapshot?.task;
      if (['completed', 'blocked', 'failed', 'stopped'].includes(result?.state)) break;
      await new Promise((resolve) => setTimeout(resolve, 500));
    }
    if (result?.outcome !== 'verified_complete')
      throw new Error('Browser workflow: ' + JSON.stringify(result));
    checks.push('real managed Browser, per-action confirmations, and page evidence');
  }
  if (process.argv.includes('--desktop') || process.argv.includes('--calculator')) {
    await application.evaluate(() => {
      const childProcess = process.getBuiltinModule('node:child_process');
      const original = childProcess.spawn;
      globalThis.paniniTestFocus = [];
      childProcess.spawn = function (file, args, options) {
        const child = original(
          file,
          args,
          args?.[0] === '--focus' ? { ...options, stdio: ['ignore', 'pipe', 'ignore'] } : options,
        );
        if (args?.[0] === '--focus') {
          const started = Date.now();
          let output = '';
          child.stdout.on('data', (value) => {
            output += value.toString().slice(0, 400);
          });
          child.on('exit', (code, signal) =>
            globalThis.paniniTestFocus.push({
              code,
              signal,
              output,
              elapsed: Date.now() - started,
            }),
          );
          child.on('error', (error) => globalThis.paniniTestFocus.push({ error: error.code }));
        }
        return child;
      };
    });
  }
  if (process.argv.includes('--desktop')) {
    fixture = spawn(
      resolve(
        root,
        'panini-core/capabilities/desktop/Panini.Desktop.Fixture/bin/Release/net10.0-windows/Panini.Desktop.Fixture.exe',
      ),
      [],
      { windowsHide: true, stdio: 'ignore' },
    );
    let target;
    for (let attempt = 0; attempt < 20; attempt++) {
      const available = await page.evaluate(() =>
        window.panini.invoke('targets', { capability: 'desktop' }),
      );
      target = available.targets.find((target) => target.application === 'Panini.Desktop.Fixture');
      if (target) break;
      await new Promise((resolve) => setTimeout(resolve, 250));
    }
    if (!target) throw new Error('Windows fixture was not enumerated');
    await page.evaluate(
      (choice) => window.panini.invoke('select', { capability: 'desktop', choice_id: choice }),
      target.choice_id,
    );
    await application.evaluate(({ dialog, BrowserWindow }) => {
      dialog.showMessageBox = async (_window, options) => {
        let review;
        try {
          review = JSON.parse(options.detail?.split('Data: ')[1]);
        } catch {}
        const action = review?.action;
        const safe =
          options.title === 'PANINI confirmation' &&
          options.message === 'PANINI verification fixture' &&
          review?.goal?.expected === 'PANINI native smoke' &&
          ((action?.kind === 'click' && !!action.element_id) ||
            (action?.kind === 'keypress' && JSON.stringify(action.keys) === '["CTRL","A"]') ||
            (action?.kind === 'type' && action.text === 'PANINI native smoke'));
        return { response: safe ? 1 : 0, checkboxChecked: false };
      };
      BrowserWindow.getAllWindows()[0].show();
      BrowserWindow.getAllWindows()[0].focus();
    });
    await page.evaluate(() =>
      window.panini.invoke('turn', {
        capability: 'desktop',
        text: 'Set the Draft text field to exactly PANINI native smoke. Do not click Apply and do not save a file.',
      }),
    );
    let result;
    for (let attempt = 0; attempt < 180; attempt++) {
      const value = await page.evaluate(() => window.panini.invoke('status'));
      result = value.snapshot?.task;
      if (['completed', 'blocked', 'failed', 'stopped'].includes(result?.state)) break;
      await new Promise((resolve) => setTimeout(resolve, 500));
    }
    if (result?.outcome !== 'verified_complete')
      throw new Error(
        'Native workflow: ' +
          JSON.stringify({
            result,
            focus: await application.evaluate(() => globalThis.paniniTestFocus),
          }),
      );
    checks.push(
      'actual Windows UIA draft edit, Jev admission, trusted confirmation and exact readback',
    );
  }
  if (process.argv.includes('--calculator')) {
    if (calculatorProcesses().length)
      throw new Error(
        'Close Calculator before this isolated test; existing user windows are never modified.',
      );
    spawn(resolve(process.env.SystemRoot || 'C:/Windows', 'System32/calc.exe'), [], {
      windowsHide: true,
      stdio: 'ignore',
    });
    let target;
    for (let attempt = 0; attempt < 30; attempt++) {
      const values = calculatorProcesses();
      if (values.length === 1) {
        calculatorPid = values[0].Id;
        calculatorCreated = values[0].StartTime;
      }
      const available = await page.evaluate(() =>
        window.panini.invoke('targets', { capability: 'desktop' }),
      );
      target = available.targets.find((target) => /^calculator(app)?$/i.test(target.application));
      if (target && calculatorPid) break;
      await new Promise((resolve) => setTimeout(resolve, 300));
    }
    if (!target || !calculatorPid)
      throw new Error('The isolated Calculator window was not available');
    await page.evaluate(
      (choice) => window.panini.invoke('select', { capability: 'desktop', choice_id: choice }),
      target.choice_id,
    );
    await page.evaluate(() =>
      window.panini.invoke('turn', {
        capability: 'desktop',
        text: 'In Standard Calculator, calculate 17 multiplied by 23. Verify the displayed result is 391.',
      }),
    );
    let result;
    for (let attempt = 0; attempt < 600; attempt++) {
      const value = await page.evaluate(() => window.panini.invoke('status'));
      result = value.snapshot?.task;
      if (['completed', 'blocked', 'failed', 'stopped'].includes(result?.state)) break;
      await new Promise((resolve) => setTimeout(resolve, 500));
    }
    if (result?.outcome !== 'verified_complete')
      throw new Error(
        'Calculator workflow: ' +
          JSON.stringify({
            result,
            focus: await application.evaluate(() => globalThis.paniniTestFocus),
          }),
      );
    checks.push(
      'actual Windows Calculator 17 × 23 = 391 through model planning, Jev admission and UIA result evidence',
    );
  }
  if (process.argv.includes('--crash')) {
    await page.evaluate(() =>
      window.panini.invoke('turn', {
        capability: 'search',
        text: 'Find the Windows UI Automation documentation',
      }),
    );
    await application.evaluate(({ BrowserWindow }) => {
      BrowserWindow.getAllWindows()
        .find((window) => window.webContents.getURL().startsWith('panini://app/'))
        .webContents.forcefullyCrashRenderer();
    });
    let recovered;
    for (let attempt = 0; attempt < 40; attempt++) {
      recovered = await viewer.evaluate(async () => {
        const value = JSON.parse(sessionStorage.getItem('panini.activity'));
        return (await fetch(`/api/panini/sessions/${value.sessionId}`)).json();
      });
      if (recovered.state === 'suspended' && recovered.task?.state === 'stopped') break;
      await new Promise((resolve) => setTimeout(resolve, 250));
    }
    if (
      recovered.state !== 'suspended' ||
      recovered.controller_client_id ||
      recovered.task?.state !== 'stopped' ||
      recovered.unsafe
    )
      throw new Error('Renderer failure did not release authority and stop execution');
    checks.push(
      'renderer crash fences the controller, stops the active worker and retains read-only activity',
    );
  } else if (process.argv.includes('--companion-crash')) {
    const mainPid = await application.evaluate(() => process.pid);
    if (!Number.isInteger(mainPid)) throw new Error('Missing owned Electron process');
    const script = `$processes = @(Get-CimInstance Win32_Process); $hosts = @($processes | Where-Object { $_.ParentProcessId -eq ${mainPid} -and $_.Name -eq 'Panini.RuntimeHost.exe' }); $brokers = @($processes | Where-Object { $_.Name -eq 'Panini.Desktop.Broker.exe' -and $_.ParentProcessId -in $hosts.ProcessId }); if ($brokers.Count -ne 1) { throw 'Owned broker not uniquely identified' }; $brokers[0].ProcessId`;
    const brokerPid = Number(
      execFileSync('powershell.exe', ['-NoProfile', '-Command', script], {
        windowsHide: true,
        encoding: 'utf8',
      }).trim(),
    );
    if (!Number.isInteger(brokerPid) || brokerPid <= 0)
      throw new Error('Invalid owned broker identity');
    process.kill(brokerPid);
    await page.getByText('A companion stopped. Quit and reopen PANINI.', { exact: true }).waitFor();
    for (const port of [3200, 8200]) {
      let closed = false;
      for (let attempt = 0; attempt < 30; attempt++) {
        try {
          await fetch(`http://127.0.0.1:${port}/api/panini/health`, {
            signal: AbortSignal.timeout(500),
          });
        } catch {
          closed = true;
          break;
        }
        await new Promise((resolve) => setTimeout(resolve, 250));
      }
      if (!closed) throw new Error('A runtime companion survived peer failure');
    }
    checks.push('unexpected native broker exit closes all owned runtime companions immediately');
  } else {
    await page.evaluate(() => window.panini.invoke('end'));
    checks.push('conversation teardown');
  }
  if (viewer) await viewer.screenshot({ path: resolve(output, 'observer-ended.png') });
  if (errors.length) throw new Error(errors.join('\n'));
  await writeFile(resolve(output, 'result.json'), JSON.stringify({ checks, errors }, null, 2));
  console.log(JSON.stringify({ checks, errors }));
} catch (error) {
  const pages = application.windows();
  if (pages[0]) {
    await pages[0].screenshot({ path: resolve(output, 'failure.png') }).catch(() => {});
    await writeFile(
      resolve(output, 'events.json'),
      JSON.stringify(await pages[0].evaluate(() => window.paniniTestEvents), null, 2),
    );
  }
  console.error(error);
  process.exitCode = 1;
} finally {
  fixture?.kill();
  if (
    calculatorPid &&
    calculatorProcesses().some(
      (value) => value.Id === calculatorPid && value.StartTime === calculatorCreated,
    )
  )
    process.kill(calculatorPid);
  await application.close();
}
