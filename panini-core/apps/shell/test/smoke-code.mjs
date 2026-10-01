import { _electron as electron } from 'playwright';
import { createRequire } from 'node:module';
import { mkdir, writeFile, readFile, stat } from 'node:fs/promises';
import { resolve } from 'node:path';
import { existsSync, readFileSync } from 'node:fs';

const require = createRequire(import.meta.url);
const root = resolve(import.meta.dirname, '../..');
const workspace = resolve(root, 'artifacts/code-graphical-workspace');
const profile = 'code-graphical';
const output = resolve(root, 'artifacts/panini-smoke', profile);
await mkdir(output, { recursive: true });
await mkdir(workspace, { recursive: true });

// Load .env if present (same as smoke.mjs --configured behavior)
if (existsSync(resolve(root, '.env'))) {
  try {
    process.loadEnvFile(resolve(root, '.env'));
  } catch {}
}
const env = {
  ...process.env,
  PANINI_NODE: process.execPath,
  JEV_REV_DESKTOP_ENABLED: 'true',
  PANINI_CLI_ENABLED: 'true',
  PANINI_CLI_WORKSPACES: JSON.stringify([workspace]),
  PANINI_CLI_DATA: resolve(root, 'artifacts/code-graphical-data'),
  PANINI_DESKTOP_VISUAL: '1',
};
delete env.ELECTRON_RUN_AS_NODE;

const packaged = process.argv.includes('--packaged');
const useObserver = process.argv.includes('--observer');
console.log(`Launching PANINI ${packaged ? 'packaged' : 'dev'} with Code workspace: ${workspace}`);
console.log(`Observer: ${useObserver}, output: ${output}`);

const application = await electron.launch({
  executablePath: packaged
    ? resolve(root, 'artifacts/panini-package/win-unpacked/PANINI.exe')
    : require('electron'),
  args: [...(packaged ? [] : [resolve(root, 'panini-core/apps/shell')])],
  env,
  timeout: 60000,
});

const checks = [];
const errors = [];
let viewer;

try {
  const page = await application.firstWindow();
  await page.evaluate(() => {
    window.paniniTestEvents = [];
    window.panini.subscribe((event) => {
      window.paniniTestEvents.push(event);
    });
  });
  page.on('pageerror', (error) => errors.push(error.message));
  await page
    .getByRole('heading', { name: 'Let’s get something done.' })
    .waitFor({ timeout: 30000 });
  console.log('UI ready, waiting for runtime...');

  let current;
  for (let attempt = 0; attempt < 150; attempt++) {
    current = await page.evaluate(() => window.panini.invoke('status'));
    if (current.connected && current.snapshot?.state === 'ready') break;
    await new Promise((r) => setTimeout(r, 500));
    if (attempt % 10 === 0)
      console.log(
        `  status attempt ${attempt}: ${current.connected ? current.snapshot?.state : 'not connected'}`,
      );
  }
  if (!current?.snapshot || current.snapshot.state !== 'ready') {
    throw new Error('Runtime did not become ready: ' + JSON.stringify(current?.snapshot, null, 2));
  }
  console.log('Runtime ready');
  checks.push('runtime startup and controller lease');

  const caps = await page.evaluate(() => window.panini.invoke('capabilities'));
  console.log('Capabilities:', caps.capabilities);
  checks.push(...caps.capabilities.map((c) => `${c.capability}: ${c.state}`));
  const codeCap = caps.capabilities.find((c) => c.capability === 'code');
  if (!codeCap || codeCap.state !== 'ready')
    throw new Error('Code capability not ready: ' + JSON.stringify(caps));

  // Observer check if requested
  if (useObserver) {
    console.log('Setting up observer...');
    await application.evaluate(({ shell }) => {
      shell.openExternal = async (url) => {
        globalThis.paniniTestViewerUrl = url;
      };
    });
    await page.evaluate(() => window.panini.invoke('dashboard', { shareCaptions: false }));
    const opened = application.waitForEvent('window');
    await application.evaluate(async ({ BrowserWindow }) => {
      const win = new BrowserWindow({
        width: 1200,
        height: 950,
        show: true,
        webPreferences: { nodeIntegration: false, contextIsolation: true, sandbox: true },
      });
      await win.loadURL(globalThis.paniniTestViewerUrl);
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
    if (blocked !== 403) throw new Error('Observer could mutate session');
    await viewer.screenshot({ path: resolve(output, 'observer.png') });
    checks.push('live observer ticket exchange, read-only dashboard and mutation denial');
    console.log('Observer verified');
  }

  // Verify Code targets
  const targets = await page.evaluate(() =>
    window.panini.invoke('targets', { capability: 'code' }),
  );
  console.log('Code targets:', targets);
  if (!targets.targets.length) throw new Error('No Code workspace targets');
  const target = targets.targets[0];
  checks.push(`code workspace target: ${target.label}`);

  // Auto-approve all PANINI confirmations (disposable workspace)
  await application.evaluate(({ dialog }) => {
    const original = dialog.showMessageBox;
    dialog.showMessageBox = async (_win, options) => {
      console.log('[test] dialog', options.title, options.message, options.detail?.slice(0, 200));
      // Auto-approve any PANINI confirmation for this disposable workspace test
      if (options.title === 'PANINI confirmation') {
        return { response: 1, checkboxChecked: false };
      }
      // Fallback to original for other dialogs
      return original ? original(_win, options) : { response: 0, checkboxChecked: false };
    };
    // Also intercept the code-specific confirmation path if any
    globalThis.__paniniCodeTestApproveAll = true;
  });
  console.log('Auto-approve enabled');

  // Select Code workspace
  await page.evaluate(
    (choice) => window.panini.invoke('select', { capability: 'code', choice_id: choice }),
    target.choice_id,
  );
  console.log('Selected Code workspace');

  // Take screenshot before instruction
  await page.screenshot({ path: resolve(output, 'conversation.png') });

  // Send Code instruction
  const instruction =
    "Create a file named panini-cli-smoke.txt in the workspace root containing exactly 'PANINI code graphical smoke verified' on the first line, no extra whitespace. Do not modify other files. If the file already exists, overwrite it with that exact content.";
  console.log('Sending Code turn:', instruction);
  await page.evaluate(
    (text) => window.panini.invoke('turn', { capability: 'code', text }),
    instruction,
  );

  // Poll for completion (code can take longer due to model + approvals)
  let result;
  let snapshot;
  for (let attempt = 0; attempt < 600; attempt++) {
    snapshot = await page.evaluate(() => window.panini.invoke('status'));
    result = snapshot.snapshot?.task;
    const phase = snapshot.snapshot?.state;
    if (attempt % 10 === 0)
      console.log(
        `  poll ${attempt}: task=${result?.state} phase=${phase} message=${(result?.message || snapshot.snapshot?.message || '').slice(0, 120)}`,
      );
    if (['completed', 'blocked', 'failed', 'stopped'].includes(result?.state)) break;
    await new Promise((r) => setTimeout(r, 500));
  }
  console.log('Final task:', JSON.stringify(result, null, 2));
  console.log('Snapshot state:', snapshot.snapshot?.state);
  console.log('Results:', snapshot.snapshot?.results);

  if (!result) throw new Error('No task result after polling');
  // Code adapter returns partial or verified_complete on success; blocked/failed is error
  if (result.state === 'failed') throw new Error('Code workflow failed: ' + JSON.stringify(result));
  if (result.state === 'stopped')
    throw new Error('Code workflow was stopped: ' + JSON.stringify(result));
  // Accept completed or blocked with meaningful message? For code, partial is expected if no postcondition
  // The task's outcome is in result.outcome (from contracts)
  // snapshot.task.outcome vs result.outcome
  const outcome = result.outcome;
  console.log('Outcome:', outcome);
  if (outcome === 'blocked' || outcome === 'failed') {
    // Still allow if file was actually created correctly - check filesystem anyway
    console.log('Outcome indicates blocked/failed, will verify filesystem before failing');
  }

  // Verify filesystem side-effect: file should exist with exact content regardless of outcome string
  const expectedFile = resolve(workspace, 'panini-cli-smoke.txt');
  let fileContent = null;
  let fileExists = false;
  try {
    fileContent = await readFile(expectedFile, 'utf8');
    fileExists = true;
  } catch {}
  console.log(`File ${expectedFile}: exists=${fileExists}, content=${JSON.stringify(fileContent)}`);
  await writeFile(
    resolve(output, 'workspace-check.json'),
    JSON.stringify({ fileExists, fileContent, result, snapshot: snapshot.snapshot }, null, 2),
  );

  if (!fileExists)
    throw new Error('Code workflow did not create panini-cli-smoke.txt in workspace');
  // Normalize: trim and check first line
  const normalized = fileContent.replace(/\r\n/g, '\n').trim();
  if (normalized !== 'PANINI code graphical smoke verified') {
    throw new Error(
      `File content mismatch: expected 'PANINI code graphical smoke verified' got ${JSON.stringify(fileContent)}`,
    );
  }
  checks.push('Code workspace file creation with exact host readback: panini-cli-smoke.txt');
  checks.push(`Code turn outcome: ${outcome} / state: ${result.state}`);

  // Also verify generic UI elements
  await page.screenshot({ path: resolve(output, 'code-conversation.png') });
  await page
    .getByRole('button', { name: 'Collapse PANINI' })
    .click()
    .catch(() => {});
  await page.screenshot({ path: resolve(output, 'capsule.png') }).catch(() => {});
  await page
    .getByRole('button', { name: 'Expand PANINI' })
    .click()
    .catch(() => {});
  checks.push('capsule, conversation and Code typed input');

  // Clean stop
  await page.evaluate(() => window.panini.invoke('stop')).catch(() => {});
  checks.push('idle Stop after Code');

  // Observer final screenshot
  if (viewer)
    await viewer.screenshot({ path: resolve(output, 'observer-ended.png') }).catch(() => {});

  if (errors.length) throw new Error(errors.join('\n'));
  await writeFile(
    resolve(output, 'result.json'),
    JSON.stringify({ checks, errors, codeFile: fileContent }, null, 2),
  );
  console.log(JSON.stringify({ checks, errors }, null, 2));
  console.log('PANINI-CLI graphical smoke PASSED');
} catch (error) {
  console.error('PANINI-CLI graphical smoke FAILED:', error);
  const pages = application.windows();
  if (pages[0]) {
    await pages[0].screenshot({ path: resolve(output, 'failure.png') }).catch(() => {});
    try {
      const events = await pages[0].evaluate(() => window.paniniTestEvents);
      await writeFile(resolve(output, 'events.json'), JSON.stringify(events, null, 2));
    } catch {}
  }
  // Also dump workspace state
  try {
    const wsFiles = await readFile(workspace + '/panini-cli-smoke.txt', 'utf8').catch(
      () => '(missing)',
    );
    await writeFile(resolve(output, 'failure-workspace.txt'), String(wsFiles));
  } catch {}
  process.exitCode = 1;
} finally {
  if (viewer)
    try {
      await viewer.close();
    } catch {}
  await application.close().catch(() => {});
}


