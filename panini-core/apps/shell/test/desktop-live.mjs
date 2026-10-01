// Live replay of the Paint UAT through the real app: "Open Paint and draw a simple elephant".
// Uses the real planner, Jev checks and Windows companion (a few cents of model calls). It takes the
// real mouse: keep your hands off it while it runs. Evidence: artifacts/desktop-live/*.png and events.json.
//
// Run: node panini-core/apps/shell/test/desktop-live.mjs ["custom instruction"]
import { _electron as electron } from 'playwright';
import { createRequire } from 'node:module';
import { spawnSync } from 'node:child_process';
import { mkdir, writeFile, appendFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import assert from 'node:assert/strict';

const require = createRequire(import.meta.url);
const root = resolve(import.meta.dirname, '../../../..');
const output = resolve(root, 'artifacts/desktop-live');
await mkdir(output, { recursive: true });
process.loadEnvFile(resolve(root, '.env'));
const instruction = process.argv[2] || 'Open Paint and draw a simple elephant.';
const env = { ...process.env, PANINI_NODE: process.execPath, PANINI_DEBUG_LEASE: '1' };
delete env.ELECTRON_RUN_AS_NODE;
const python = resolve(root, '.venv/Scripts/python.exe');

function screenshot(name) {
  const path = resolve(output, name);
  spawnSync(
    python,
    [
      '-c',
      `from PIL import ImageGrab; ImageGrab.grab(all_screens=True).save(r"${path}")`,
    ],
    { windowsHide: true },
  );
  return path;
}

const application = await electron.launch({
  executablePath: require('electron'),
  args: [resolve(root, 'panini-core/apps/shell')],
  env,
  timeout: 60000,
});
const checks = [];
// The app's own output (shell, backend, companion): the first place to look when a run stops early.
const runtimeLog = resolve(output, 'runtime.log');
await writeFile(runtimeLog, '');
for (const stream of [application.process().stdout, application.process().stderr])
  stream?.on('data', (chunk) => void appendFile(runtimeLog, chunk).catch(() => {}));
try {
  const page = await application.firstWindow();
  let status;
  for (let attempt = 0; attempt < 150; attempt++) {
    status = await page.evaluate(() => window.panini.invoke('status'));
    if (status.connected && status.snapshot?.state === 'ready') break;
    await new Promise((done) => setTimeout(done, 500));
  }
  assert.ok(status.connected && status.snapshot?.state === 'ready', 'Runtime did not become ready');
  const caps = await page.evaluate(() => window.panini.invoke('capabilities'));
  const desktop = caps.capabilities.find((c) => c.capability === 'desktop');
  assert.equal(desktop?.state, 'ready', `Desktop capability: ${JSON.stringify(desktop)}`);
  assert.ok(desktop.graphical_actions.includes('draw'), 'The companion does not advertise draw');
  checks.push('Desktop ready; the companion advertises the draw action');
  await page.evaluate(() => {
    window.uatEvents = [];
    window.panini.subscribe((event) => window.uatEvents.push(event));
  });
  // The test approves the confirmations for this exact request and logs each one.
  const approvals = [];
  await application.evaluate(({ dialog }) => {
    globalThis.uatDialogs = [];
    dialog.showMessageBox = async (_window, options) => {
      globalThis.uatDialogs.push({ message: options.message, detail: options.detail?.slice(0, 300) });
      return { response: options.title === 'PANINI confirmation' ? 1 : 0, checkboxChecked: false };
    };
  });

  await page.evaluate((text) => window.panini.invoke('turn', { text }), instruction);
  console.log('sent:', instruction);
  let task;
  let tick = 0;
  const started = Date.now();
  for (let attempt = 0; attempt < 960; attempt++) {
    const value = await page.evaluate(() => window.panini.invoke('status'));
    task = value.snapshot?.task;
    if (attempt % 8 === 0 && Date.now() - started > 4000) screenshot(`tick-${String(tick++).padStart(2, '0')}.png`);
    if (['completed', 'blocked', 'failed', 'stopped', 'ready', 'needs_input'].includes(task?.state)) break;
    await new Promise((done) => setTimeout(done, 500));
  }
  const final = screenshot('final.png');
  const events = await page.evaluate(() => window.uatEvents);
  const dialogs = await application.evaluate(() => globalThis.uatDialogs);
  await writeFile(resolve(output, 'events.json'), JSON.stringify({ task, dialogs, events }, null, 2));
  console.log(JSON.stringify({ state: task?.state, outcome: task?.outcome, message: task?.message }, null, 2));
  console.log('confirmations shown:', dialogs.map((d) => `${d.message}: ${d.detail?.split('\n')[0]}`));
  const activity = events.filter((e) => e.type === 'computer.activity' || e.type === 'task.computer_activity');
  const drew = events.some((e) => JSON.stringify(e).includes('"kind":"draw"'));
  console.log(`events: ${events.length}; computer activity: ${activity.length}; draw dispatched: ${drew}`);
  assert.ok(drew, 'No draw action was dispatched');
  checks.push('a draw action was planned, approved and dispatched');
  assert.ok(['verified_complete', 'partial'].includes(task?.outcome), `Outcome ${task?.outcome}: ${task?.message}`);
  checks.push(`outcome ${task.outcome}`);
  console.log(JSON.stringify({ checks, final }, null, 2));
} finally {
  await application.close();
}
