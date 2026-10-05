// Real browser recording/replay and Admin UI; healer selection is deterministic test data.
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdir, writeFile, mkdtemp } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { chromium } from 'playwright';

const origin = 'http://127.0.0.1:3247';
const headers = { 'x-panini-console': 'teach-preview-only' };
const evidence = 'artifacts/teach-browser';
let server,
  demoBrowser,
  uiBrowser,
  diagnostics = '';
const request = async (path, method = 'GET', body) => {
  const response = await fetch(origin + path, {
    method,
    headers: { ...headers, 'Content-Type': 'application/json' },
    ...(body ? { body: JSON.stringify(body) } : {}),
  });
  assert(response.ok, await response.clone().text());
  return response.json();
};
async function approve(open, yes = true) {
  return request('/api/harness/interrupts/' + open.id, 'POST', {
    nonce: open.nonce,
    action_digest: open.action_digest,
    status: 'resolved',
    payload: { approved: yes },
  });
}
try {
  await mkdir(evidence, { recursive: true });
  demoBrowser = await chromium.launchPersistentContext(
    await mkdtemp(join(tmpdir(), 'panini-teach-browser-')),
    {
      channel: 'msedge',
      headless: true,
      args: ['--remote-debugging-port=3248'],
    },
  );
  const demo = demoBrowser.pages()[0];
  await demoBrowser.setExtraHTTPHeaders(headers);
  server = spawn('.venv/Scripts/python.exe', ['scripts/panini-teach-preview.py'], {
    windowsHide: true,
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  for (const stream of [server.stdout, server.stderr])
    stream.on('data', (data) => {
      diagnostics = (diagnostics + data).slice(-8000);
    });
  for (let i = 0; i < 120; i++) {
    try {
      if ((await fetch(origin + '/api/harness/teach', { headers })).ok) break;
    } catch {}
    if (server.exitCode !== null) throw Error(diagnostics);
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  assert.equal((await fetch(origin + '/api/harness/teach')).status, 401);
  await demo.goto(origin + '/demo');
  uiBrowser = await chromium.launch({ channel: 'chrome', headless: true });
  const ui = await uiBrowser.newPage({
    viewport: { width: 1440, height: 1000 },
    extraHTTPHeaders: headers,
  });
  const errors = [];
  ui.on('pageerror', (error) => errors.push(error.message));
  await ui.goto(origin + '/build/skills');
  await ui.getByRole('button', { name: 'Watch me' }).click();
  await ui.getByText('Do the task in the selected browser', { exact: false }).waitFor();
  const query = await demo.getByLabel('Query').boundingBox();
  await demo.mouse.click(query.x + 20, query.y + 20);
  await demo.keyboard.type('apples');
  const save = await demo.getByRole('button', { name: 'Save report' }).boundingBox();
  await demo.mouse.click(save.x + 20, save.y + 20);
  await demo.getByText('Report ready: apples', { exact: true }).waitFor();
  await ui.getByRole('button', { name: 'Stop', exact: true }).click();
  await ui.getByLabel('Skill name').fill('report-demo');
  await ui.getByLabel('What does this task do?').fill('Create a report from a query');
  await ui.getByLabel('What visible text proves the task worked?').fill('Report ready');
  const draft = await request('/api/harness/teach');
  const recording = await request('/api/harness/teach/recordings/' + draft.recordings[0].id);
  await writeFile(evidence + '/normalized-recording.json', JSON.stringify(recording, null, 2));
  assert(
    recording.replay.steps.every((s) => s.tool !== 'manual'),
    JSON.stringify(recording.replay),
  );
  assert(recording.replay.steps.some((s) => s.tool === 'browser_type'));
  assert(recording.replay.recording_artifacts.start);
  await ui.getByRole('button', { name: 'Review skill in Inbox' }).click();
  for (let i = 0; i < 40 && !(await request('/api/harness/interrupts')).interrupts?.length; i++)
    await new Promise((resolve) => setTimeout(resolve, 100));
  const review = (await request('/api/harness/interrupts')).interrupts.find(
    (r) => r.reason === 'teach_version',
  );
  assert(review, 'Skill review was not opened');
  await approve(review);
  const full = await request('/api/harness/teach/skills/report-demo');
  const inputName = Object.keys(full.replay.inputs)[0];
  const firstPromise = request('/api/harness/teach/run', 'POST', {
    name: 'report-demo',
    inputs: { [inputName]: 'oranges' },
    browser: 'managed',
  });
  let firstEffect;
  for (let i = 0; i < 100; i++) {
    firstEffect = (await request('/api/harness/interrupts')).interrupts.find(
      (r) => r.reason === 'teach_effect',
    );
    if (firstEffect) break;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  assert(firstEffect, 'Save action should require review');
  await approve(firstEffect);
  const first = await firstPromise;
  if (first.state !== 'passed') console.error(JSON.stringify(await request('/preview/evidence')));
  assert.equal(first.state, 'passed', JSON.stringify(first));
  await demo.getByText('Report ready: oranges', { exact: true }).waitFor();
  // Keep the changed label through navigation so deterministic replay reaches the changed page.
  await demo.route(origin + '/demo', (route) =>
    route.fulfill({
      contentType: 'text/html',
      body: '<label>Query <input id="query"></label><button id="save">Send report</button><p id="result" role="status"></p><script>save.onclick=()=>result.textContent="Report ready: "+query.value</script>',
    }),
  );
  const repairedPromise = request('/api/harness/teach/run', 'POST', {
    name: 'report-demo',
    inputs: { [inputName]: 'pears' },
    browser: 'managed',
  });
  let effect;
  for (let i = 0; i < 200; i++) {
    effect = (await request('/api/harness/interrupts')).interrupts.find(
      (r) => r.reason === 'teach_effect',
    );
    if (effect) break;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  assert(effect, 'Communicate effect bypassed approval or replay failed');
  assert(effect.title.includes('communicate'), effect.title);
  await approve(effect);
  const repaired = await repairedPromise;
  assert.equal(repaired.state, 'passed', JSON.stringify(repaired));
  assert.equal(repaired.heals, 1);
  assert.equal((await request('/api/harness/teach/skills/report-demo')).version, 1);
  const patch = (await request('/api/harness/interrupts')).interrupts.find(
    (r) => r.reason === 'teach_version',
  );
  assert(patch, 'Passing repair did not produce owner review');
  await ui.goto(origin + '/inbox');
  await ui.getByLabel('Review demonstration skill').waitFor();
  await ui.getByText('Recorded screenshot', { exact: true }).first().click();
  await ui.waitForFunction(() =>
    [...document.querySelectorAll('img')].some(
      (img) => img.alt.startsWith('Recorded step') && img.naturalWidth > 0,
    ),
  );
  await ui.screenshot({ path: evidence + '/review-desktop.png', fullPage: true });
  await ui.getByRole('button', { name: 'Approve', exact: true }).click();
  await ui.waitForFunction(
    async () =>
      (
        await (
          await fetch('/api/harness/teach/skills/report-demo', {
            headers: { 'x-panini-console': 'teach-preview-only' },
          })
        ).json()
      ).version === 2,
  );
  await ui.goto(origin + '/build/skills');
  await ui.getByRole('button', { name: 'Open replay' }).waitFor();
  await ui.waitForTimeout(500);
  await ui.screenshot({ path: evidence + '/admin-desktop.png', fullPage: true });
  await ui.setViewportSize({ width: 412, height: 915 });
  await ui.waitForFunction(
    () => document.querySelector('aside').getBoundingClientRect().right <= 0,
  );
  await ui.screenshot({ path: evidence + '/admin-mobile.png', fullPage: true });
  assert(
    await ui.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
    'Mobile horizontal overflow',
  );
  // Native key events cannot put a credential value into the recording.
  await demo.unroute(origin + '/demo');
  await demo.goto(origin + '/demo');
  await demo.evaluate(() => {
    const p = document.createElement('input');
    p.type = 'password';
    p.id = 'login';
    p.setAttribute('aria-label', 'Login');
    document.body.append(p);
  });
  await request('/api/harness/teach/record/start', 'POST', { browser: 'managed' });
  await demo.locator('#login').focus();
  await demo.keyboard.type('orange-credential-fixture');
  assert.equal(await demo.locator('#login').inputValue(), '');
  const stopped = await request('/api/harness/teach/stop', 'POST', {});
  assert(!JSON.stringify(stopped).includes('orange-credential-fixture'));
  assert.deepEqual(stopped.replay.recording_artifacts, {});
  // A real iframe recording must keep its frame identity through review and replay.
  await request('/api/harness/teach/record/start', 'POST', { browser: 'managed' });
  await demo.goto(origin + '/demo/frame');
  const reportFrame = demo.frameLocator('iframe[name="Report"]');
  await reportFrame.getByLabel('Query').fill('apples');
  await reportFrame.getByRole('button', { name: 'Save report' }).click();
  await reportFrame.getByText('Report ready: apples').waitFor();
  await demo.waitForTimeout(600);
  const framed = await request('/api/harness/teach/stop', 'POST', {});
  await writeFile(evidence + '/framed-recording.json', JSON.stringify(framed, null, 2));
  assert(
    framed.replay.steps.some((step) => step.target?.frame),
    JSON.stringify(framed.replay),
  );
  assert(
    framed.replay.steps.every((step) => step.tool !== 'manual'),
    JSON.stringify(framed.replay),
  );
  framed.replay.expect = [
    { id: 's99', tool: 'browser_verify_text_visible', args: { text: 'Report ready' } },
  ];
  const framedProposal = await request('/api/harness/teach/propose', 'POST', {
    name: 'framed-report',
    description: 'Create a report in the named iframe',
    replay: framed.replay,
  });
  await approve(framedProposal.review);
  const framedSkill = await request('/api/harness/teach/skills/framed-report');
  const framedInput = Object.keys(framedSkill.replay.inputs)[0];
  const framedPromise = request('/api/harness/teach/run', 'POST', {
    name: 'framed-report',
    inputs: { [framedInput]: 'oranges' },
    browser: 'managed',
  });
  let framedEffect;
  for (let i = 0; i < 100; i++) {
    framedEffect = (await request('/api/harness/interrupts')).interrupts.find(
      (review) => review.reason === 'teach_effect',
    );
    if (framedEffect) break;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  if (!framedEffect)
    throw Error(
      `Framed effect did not reach review: ${JSON.stringify(await framedPromise)} ${JSON.stringify((await request('/preview/evidence')).receipts.slice(-3))}`,
    );
  await approve(framedEffect);
  const framedResult = await framedPromise;
  assert.equal(framedResult.state, 'passed', JSON.stringify(framedResult));
  await reportFrame.getByText('Report ready: oranges').waitFor();
  assert.equal(
    await demo.frameLocator('iframe[name="Other"]').getByRole('status').textContent(),
    '',
  );
  const proof = await request('/preview/evidence');
  await writeFile(evidence + '/real-browser-evidence.json', JSON.stringify(proof, null, 2));
  assert(
    proof.receipts.some((r) => r.tool === 'browser_verify_text_visible' && r.outcome === 'ok'),
  );
  assert.equal(errors.length, 0, errors.join('\n'));
  console.log(
    JSON.stringify({
      passed: true,
      real_recorder: true,
      real_registry_replay: true,
      changed_inputs: true,
      credential_input_blocked: true,
      effect_review: true,
      patch_review: true,
      real_named_iframe_replay: true,
      multiple_frame_scope: true,
      healer_provider: 'deterministic fake, no live provider qualification',
      runs: proof.runs.map((r) => ({ state: r.state, heals: r.heals })),
    }),
  );
} catch (error) {
  console.error(diagnostics);
  throw error;
} finally {
  await uiBrowser?.close();
  server?.kill();
  await demoBrowser?.close();
}
