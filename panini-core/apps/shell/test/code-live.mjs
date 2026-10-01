import { _electron as electron } from 'playwright';
import { createRequire } from 'node:module';
import { mkdir, writeFile, readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import assert from 'node:assert/strict';

const require = createRequire(import.meta.url);
const root = resolve(import.meta.dirname, '../..');
const orchestration = process.env.PANINI_ORCHESTRATION_UAT === '1';
const ambiguity = orchestration && process.argv.includes('--ambiguous');
const output = resolve(
  root,
  ambiguity
    ? 'artifacts/panini-orchestration/live-ambiguity'
    : orchestration
      ? 'artifacts/panini-orchestration/live'
      : 'artifacts/panini-cli-live-uat',
);
const workspace = resolve(output, 'workspace');
await mkdir(workspace, { recursive: true });
const secondWorkspace = resolve(output, 'second-workspace');
if (ambiguity) await mkdir(secondWorkspace, { recursive: true });
const env = {
  ...process.env,
  PANINI_NODE: process.execPath,
  PANINI_CLI_ENABLED: 'true',
  PANINI_CLI_WORKSPACES: JSON.stringify(ambiguity ? [workspace, secondWorkspace] : [workspace]),
  JEV_REV_CODE_MODEL: 'gpt-4.1',
  PANINI_CLI_REASONING_EFFORT: 'low',
  PANINI_CLI_DATA: resolve(output, 'journal'),
  PANINI_PLANNER_MODEL: 'gpt-4.1-mini',
};
delete env.ELECTRON_RUN_AS_NODE;
const application = await electron.launch({
  executablePath: require('electron'),
  args: [resolve(root, 'panini-core/apps/shell')],
  env,
  timeout: 60000,
});
const checks = [];
let events = [];
try {
  const page = await application.firstWindow();
  page.setDefaultTimeout(30000);
  let status;
  for (let attempt = 0; attempt < 100; attempt++) {
    status = await page.evaluate(() => window.panini.invoke('status'));
    if (status.connected && status.snapshot?.state === 'ready') break;
    await new Promise((resolve) => setTimeout(resolve, 300));
  }
  assert.ok(status.connected && status.snapshot?.state === 'ready', JSON.stringify(status));
  await page.evaluate(() => {
    window.uatEvents = [];
    window.panini.subscribe((e) => window.uatEvents.push(e));
  });
  // Only this harmless, exact fixture command can be approved by this test.
  await application.evaluate(({ dialog }, orchestration) => {
    dialog.showMessageBox = async (_win, options) => {
      if (
        orchestration &&
        options.message === 'Visual Studio Code' &&
        options.detail?.includes('No document, command or URL will be passed')
      )
        return { response: 1, checkboxChecked: false };
      const raw = options.detail?.split('Data: ')[1];
      let command = '';
      try {
        command = JSON.parse(raw).request.command;
      } catch {}
      const wrapped =
        '"' +
        process.env.USERPROFILE +
        '\\.cache\\codex-runtimes\\codex-primary-runtime\\dependencies\\native\\powershell\\pwsh.exe" -NoProfile -Command "Write-Output \'PANINI_CLI_LIVE_UAT\'"';
      const safe =
        command === "Write-Output 'PANINI_CLI_LIVE_UAT'" ||
        command?.replaceAll('\\\\', '\\').replace(' -NoProfile -Command ', ' -Command ') ===
          wrapped.replace(' -NoProfile -Command ', ' -Command ');
      return { response: safe ? 1 : 0, checkboxChecked: false };
    };
  }, orchestration);
  if (orchestration) {
    await page
      .getByRole('textbox', { name: 'Your instruction' })
      .fill('Run a simulation in Python using VS Code.');
    await page.getByRole('button', { name: 'Send instruction' }).click();
    await page.getByRole('heading', { name: 'One detail to continue.' }).waitFor();
    const clarification = await page.evaluate(() => window.panini.invoke('status'));
    assert.equal(clarification.snapshot.task.outcome, 'clarification');
    assert.equal(clarification.snapshot.task.steps.length, 0);
    checks.push(
      'Auto asks for the missing simulation subject without demanding a window or dispatching input',
    );
    await page.screenshot({ path: resolve(output, 'clarification.png') });
    await page.evaluate(() => {
      window.uatEvents = [];
    });
  } else {
    await page.getByRole('combobox', { name: 'Capability', exact: true }).selectOption('code');
    const targets = await page.evaluate(() =>
      window.panini.invoke('targets', { capability: 'code' }),
    );
    assert.equal(targets.targets.length, 1);
    await page
      .getByRole('combobox', { name: 'Target', exact: true })
      .selectOption(targets.targets[0].choice_id);
  }
  await page
    .getByRole('textbox', { name: 'Your instruction' })
    .fill(
      (orchestration
        ? 'Change the task: first open Visual Studio Code, then in my configured coding workspace, '
        : '') +
        "Run exactly this harmless PowerShell command: Write-Output 'PANINI_CLI_LIVE_UAT'. Do not read or modify files. Report the command output.",
    );
  await page.getByRole('button', { name: 'Send instruction' }).click();
  if (ambiguity) {
    await page.getByRole('group', { name: 'Choose where to continue' }).waitFor();
    const waiting = await page.evaluate(() => window.panini.invoke('status'));
    assert.equal(waiting.snapshot.task.steps[0].state, 'completed');
    assert.equal(waiting.snapshot.task.choices.length, 2);
    await page.screenshot({ path: resolve(output, 'target-choice.png') });
    await page.evaluate(() => {
      window.uatEvents = [];
    });
    await page
      .getByRole('group', { name: 'Choose where to continue' })
      .getByRole('button', { name: secondWorkspace, exact: true })
      .click();
    checks.push(
      'Ambiguous workspaces display concrete choices; selecting one resumes after verified app opening',
    );
  }
  await page.getByRole('region', { name: 'PANINI-CLI live execution', exact: true }).waitFor();
  checks.push('Code selected and real app-server execution visible');
  const show = page.getByRole('button', { name: 'Show details', exact: true });
  if (await show.isVisible()) await show.click();
  await page.screenshot({ path: resolve(output, 'running.png') });
  await page.waitForFunction(
    () => window.uatEvents.some((e) => e.type.startsWith('task.') && e.data?.outcome),
    null,
    { timeout: 120000 },
  );
  events = await page.evaluate(() => window.uatEvents);
  const activity = events.filter((e) => e.type === 'code.activity');
  const terminal = events.findLast((e) => e.type.startsWith('task.') && e.data?.outcome);
  assert.ok(activity.some((e) => e.data.model === 'gpt-4.1'));
  assert.ok(
    activity.some((e) =>
      e.data.items.some(
        (i) =>
          i.kind === 'commandExecution' &&
          i.exit_code === 0 &&
          i.output.includes('PANINI_CLI_LIVE_UAT'),
      ),
    ),
    JSON.stringify(terminal),
  );
  checks.push('Live command receipt: exit 0 and expected output; configured CLI model');
  assert.equal(terminal.data.outcome, 'partial');
  if (orchestration) {
    assert.deepEqual(
      terminal.data.steps.map((s) => s.capability),
      ambiguity ? ['code'] : ['desktop', 'code'],
    );
    if (!ambiguity) {
      assert.equal(terminal.data.steps[0].state, 'completed');
      assert.ok(
        events.some(
          (e) => e.type === 'target.selected' && e.data.capability === 'code' && e.data.automatic,
        ),
      );
      checks.push(
        'Actual VS Code window verified before real CLI command; sole workspace selected automatically',
      );
    } else {
      assert.ok(
        !events.some((e) => e.type === 'task.awaiting_confirmation' && e.capability === 'desktop'),
      );
      assert.ok(terminal.data.target_label.endsWith('second-workspace'));
      checks.push('Target selection does not replay the completed application opening');
    }
    const active = await page.evaluate(() => window.panini.invoke('status'));
    assert.equal(active.snapshot.task.evidence_ids.length >= (ambiguity ? 1 : 2), true);
  }
  await page.getByText('Finished · verification pending', { exact: true }).waitFor();
  checks.push('CLI completion is not presented as independently verified');
  await page.getByRole('button', { name: 'Copy output', exact: true }).click();
  const copied = await application.evaluate(({ clipboard }) => clipboard.readText());
  assert.ok(copied.includes('PANINI_CLI_LIVE_UAT'));
  checks.push('Copy output');
  await page.getByRole('button', { name: 'Open PowerShell viewer ↗', exact: true }).click();
  const userData = await application.evaluate(({ app }) => app.getPath('userData'));
  const viewer = JSON.parse(await readFile(resolve(userData, 'cli-activity.json'), 'utf8'));
  assert.ok(viewer.text.includes('PANINI_CLI_LIVE_UAT'));
  checks.push('PowerShell viewer receives the same bounded transcript');
  await page.screenshot({ path: resolve(output, 'completed.png') });
  await page.reload();
  await page.getByText('Finished · verification pending', { exact: true }).waitFor();
  assert.ok(
    (await page.getByRole('region', { name: 'Command output', exact: true }).innerText()).includes(
      'PANINI_CLI_LIVE_UAT',
    ),
  );
  checks.push('Renderer reload restores activity from the authoritative snapshot');
  await page.getByRole('combobox', { name: 'Capability', exact: true }).selectOption('code');
  await page
    .getByRole('textbox', { name: 'Your instruction' })
    .fill(
      'Explain what a command exit code means in one paragraph. Do not run commands or modify files.',
    );
  await page.getByRole('button', { name: 'Send instruction' }).click();
  await page.getByRole('button', { name: 'Stop execution', exact: true }).click();
  await page
    .getByRole('status')
    .filter({ hasText: /^Stopped$/ })
    .waitFor();
  const stopped = await page.evaluate(() => window.panini.invoke('status'));
  assert.equal(stopped.snapshot.task.outcome, 'cancelled');
  assert.equal(stopped.snapshot.unsafe, false);
  checks.push('Stop cancels the actual managed coding task and confirms executor cleanup');
  await page.screenshot({ path: resolve(output, 'stopped.png') });
  await writeFile(
    resolve(output, 'result.json'),
    JSON.stringify({ status: 'passed', checks }, null, 2),
  );
  console.log(JSON.stringify({ status: 'passed', checks }, null, 2));
} catch (error) {
  events = await (
    await application.firstWindow()
  )
    .evaluate(() => window.uatEvents || [])
    .catch(() => []);
  await writeFile(
    resolve(output, 'failure.json'),
    JSON.stringify({ error: String(error), checks, events }, null, 2),
  );
  throw error;
} finally {
  await application.close();
}


