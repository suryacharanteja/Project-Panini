// Built Admin UI with fixture API responses. Native recording is tested separately.
import assert from 'node:assert/strict';
import { readFile, mkdir, writeFile } from 'node:fs/promises';
import { createServer } from 'node:http';
import { resolve, extname, sep } from 'node:path';
import { chromium } from 'playwright';

const root = resolve('frontend/public/admin');
const evidence = 'artifacts/teach-desktop';
const server = createServer(async (req, res) => {
  const url = new URL(req.url, 'http://localhost');
  const path = resolve(
    root,
    '.' + (url.pathname.startsWith('/admin/') ? url.pathname.slice(6) : '/index.html'),
  );
  if (!path.startsWith(root + sep)) {
    res.writeHead(403).end();
    return;
  }
  try {
    const mime = {
      '.html': 'text/html',
      '.js': 'application/javascript',
      '.css': 'text/css',
      '.svg': 'image/svg+xml',
    }[extname(path)];
    res
      .writeHead(200, { 'Content-Type': mime ?? 'application/octet-stream' })
      .end(await readFile(path));
  } catch {
    res.writeHead(404).end();
  }
});
await new Promise((done) => server.listen(0, '127.0.0.1', done));
let browser;
try {
  await mkdir(evidence, { recursive: true });
  browser = await chromium.launch({ channel: 'chrome', headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const errors = [],
    requests = [];
  page.on('pageerror', (e) => errors.push(e.message));
  const draft = {
    kind: 'desktop',
    desktop_application: 'fixture',
    version: 1,
    steps: [
      {
        id: 's1',
        tool: 'desktop_type',
        target: { role: 'textbox', name: 'Draft text', ancestors: [] },
        args: { text: '{{draft}}' },
      },
    ],
    inputs: { draft: { default: 'Hello café', kind: 'text', required: true } },
    expect: [],
    recording_artifacts: {},
  };
  let recording = { state: 'idle' },
    saved = false,
    enabled = true,
    replaying = false;
  let finishReplay;
  await page.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname;
    const body = route.request().postDataJSON();
    if (body) requests.push({ path, body });
    let data = {};
    if (path === '/api/harness/teach')
      data = {
        enabled,
        recording,
        replaying,
        recordings: [],
        skills: saved
          ? [
              {
                name: 'windows-demo',
                runs: 0,
                passes: 0,
                heals: 0,
                versions: [{ version: 1, state: 'approved' }],
              },
            ]
          : [],
      };
    else if (path.endsWith('/teach/windows'))
      data = {
        windows: [
          {
            choice_id: 'fixture-window',
            application: 'fixture',
            label: 'PANINI verification fixture',
          },
          { choice_id: 'other-window', application: 'other', label: 'Other app' },
        ],
      };
    else if (path.endsWith('/teach/record/start')) {
      assert.deepEqual(body, { kind: 'desktop', window: 'fixture-window' });
      recording = { state: 'recording', kind: 'desktop', application: 'fixture' };
      data = recording;
    } else if (path.endsWith('/teach/stop')) {
      if (replaying) {
        replaying = false;
        finishReplay?.();
        data = { state: 'idle' };
      } else {
        recording = { state: 'idle' };
        data = { state: 'draft', replay: draft };
      }
    } else if (path.endsWith('/teach/propose')) {
      assert.equal(body.replay.expect[0].tool, 'desktop_verify_text');
      assert.equal(body.replay.expect[0].args.text, 'Draft applied');
      assert.equal(body.replay.desktop_application, 'fixture');
      saved = true;
      data = { review: { id: 'fixture' } };
    } else if (path.endsWith('/teach/skills/windows-demo'))
      data = { name: 'windows-demo', version: 1, replay: draft };
    else if (path.endsWith('/teach/run')) {
      assert.deepEqual(body, {
        name: 'windows-demo',
        inputs: { draft: 'New input' },
        browser: 'managed',
        window: 'fixture-window',
      });
      replaying = true;
      await new Promise((done) => {
        finishReplay = done;
      });
      data = { state: 'interrupted', failure: 'Owner stopped replay.' };
    } else if (path === '/api/harness/skills') data = { skills: [] };
    else if (path.endsWith('/interrupts')) data = { interrupts: [] };
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify(data) });
  });
  await page.goto(`http://127.0.0.1:${server.address().port}/build/skills`);
  await page.getByLabel('Recording source').selectOption('desktop');
  assert.equal(
    await page.getByRole('button', { name: 'Watch me', exact: true }).isDisabled(),
    true,
  );
  await page.getByRole('button', { name: 'Refresh windows' }).click();
  await page.getByLabel('Recording Windows window').selectOption('fixture-window');
  await page.getByRole('button', { name: 'Watch me', exact: true }).click();
  await page.getByText('Do the task in the selected Windows window', { exact: false }).waitFor();
  // Revoking the feature must never remove Stop from the owner's recording controls.
  enabled = false;
  await page.getByRole('button', { name: 'Refresh', exact: true }).click();
  assert.equal(await page.getByRole('button', { name: 'Stop', exact: true }).isDisabled(), false);
  await page.getByRole('button', { name: 'Stop', exact: true }).click();
  enabled = true;
  await page.getByRole('button', { name: 'Refresh', exact: true }).click();
  await page.getByLabel('Skill name').fill('windows-demo');
  await page.getByLabel('What does this task do?').fill('Apply a Windows draft');
  await page.getByLabel('What visible text proves the task worked?').fill('Draft applied');
  await page.screenshot({ path: evidence + '/admin-desktop.png', fullPage: true });
  await page.setViewportSize({ width: 412, height: 915 });
  await page.waitForFunction(
    () => document.querySelector('aside').getBoundingClientRect().right <= 0,
  );
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  await page.screenshot({ path: evidence + '/admin-mobile.png', fullPage: true });
  await page.getByRole('button', { name: 'Review skill in Inbox' }).click();
  await page.getByRole('button', { name: 'Open replay' }).click();
  const picker = page.getByLabel('Replay Windows window');
  assert.equal(await picker.locator('option[value="other-window"]').count(), 0);
  assert.equal(await page.getByRole('button', { name: 'Replay skill' }).isDisabled(), true);
  await picker.selectOption('fixture-window');
  await page.getByLabel('draft', { exact: true }).fill('New input');
  await page.getByRole('button', { name: 'Replay skill' }).click();
  await page.getByRole('button', { name: 'Stop', exact: true }).waitFor({ state: 'visible' });
  await page.waitForFunction(() =>
    [...document.querySelectorAll('button')].some(
      (b) => b.textContent.trim() === 'Stop' && !b.disabled,
    ),
  );
  await page.getByRole('button', { name: 'Stop', exact: true }).click();
  await page.getByText('Owner stopped replay.', { exact: false }).waitFor();
  assert.deepEqual(errors, []);
  const report = {
    passed: true,
    api: 'fixture responses; native proof is separate',
    desktopWindowSelected: true,
    desktopExpectation: true,
    replayBoundToReviewedApp: true,
    stopAfterRevocation: true,
    stopDuringReplay: true,
    mobileNoOverflow: true,
    requests,
  };
  await writeFile(evidence + '/ui-evidence.json', JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report));
} finally {
  await browser?.close();
  await new Promise((done) => server.close(done));
}
