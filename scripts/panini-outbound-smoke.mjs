import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdir } from 'node:fs/promises';
import { chromium } from 'playwright';

const origin = 'http://127.0.0.1:3236';
const headers = { 'x-panini-console': 'panini-outbound-preview-only' };
const evidence = process.env.PANINI_OUTBOUND_EVIDENCE_DIR || 'artifacts/outbound';
const server = spawn('.venv/Scripts/python.exe', ['scripts/panini-outbound-preview.py'], {
  windowsHide: true,
  stdio: ['ignore', 'pipe', 'pipe'],
});
let diagnostics = '',
  browser;
for (const stream of [server.stdout, server.stderr])
  stream.on('data', (data) => (diagnostics = (diagnostics + data).slice(-5000)));
const request = async (path, method = 'GET', body) => {
  const response = await fetch(origin + path, {
    method,
    headers: { ...headers, 'Content-Type': 'application/json' },
    ...(body ? { body: JSON.stringify(body) } : {}),
  });
  assert(response.ok, await response.clone().text());
  return response.json();
};
try {
  let ready = false;
  for (let i = 0; i < 100; i++) {
    try {
      ready = (await fetch(origin + '/api/harness/outbound', { headers })).ok;
    } catch {}
    if (ready) break;
    if (server.exitCode !== null) throw new Error(diagnostics);
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  assert(ready, diagnostics);
  browser = await chromium.launch({ channel: 'chrome', headless: true });
  const page = await browser.newPage({
    viewport: { width: 1440, height: 1000 },
    extraHTTPHeaders: headers,
  });
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.goto(origin + '/build/phone');
  await page.getByLabel('TLS SIP provider').fill('sips:sip.provider.example:5061');
  await page.getByLabel('Outbound caller number').fill('+14155552672');
  await page.getByLabel('Owner takeover number').fill('+14155552673');
  await page.getByLabel('SIP username', { exact: true }).fill('preview');
  await page.getByLabel('SIP password', { exact: true }).fill('fake-preview-password');
  for (const label of [
    'Personal tasks only;',
    'PANINI must disclose',
    'I am responsible',
    'Calls must respect',
    'I approve every',
  ])
    await page.getByLabel(label, { exact: false }).check();
  await page.getByRole('button', { name: 'Save outbound setup' }).click();
  await page.getByRole('button', { name: 'Send brief to Inbox' }).waitFor({ state: 'visible' });
  await page.waitForFunction(() =>
    [...document.querySelectorAll('button')].some(
      (button) => button.textContent === 'Send brief to Inbox' && !button.disabled,
    ),
  );
  assert.equal(await page.getByLabel('SIP password', { exact: true }).inputValue(), '');
  for (const [label, value] of [
    ['Callee name', 'Dental preview'],
    ['Destination number', '+14155552671'],
    ['Call purpose', 'Ask about afternoon appointments'],
    ['Your first name', 'Alex'],
    ['Facts PANINI may disclose', 'First name Alex'],
    ['Acceptable outcomes', 'An afternoon slot next week'],
  ])
    await page.getByLabel(label, { exact: false }).fill(value);
  await page.getByRole('button', { name: 'Add availability window' }).click();
  const availabilityTime = (value) => {
    const date = new Date(value);
    return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
  };
  await page
    .getByLabel('Availability start 1 (your local timezone)')
    .fill(availabilityTime('2026-10-08T20:00:00Z'));
  await page
    .getByLabel('Availability end 1 (your local timezone)')
    .fill(availabilityTime('2026-10-08T21:00:00Z'));
  await mkdir(evidence, { recursive: true });
  await page.setViewportSize({ width: 412, height: 915 });
  await page.waitForFunction(
    () => document.querySelector('aside').getBoundingClientRect().right <= 0,
  );
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  await page
    .getByRole('group', { name: 'Permitted availability checks' })
    .screenshot({ path: `${evidence}/availability-mobile.png` });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.getByRole('button', { name: 'Send brief to Inbox' }).click();
  await page.getByRole('button', { name: 'Edit unapproved brief' }).waitFor();
  let status = await request('/api/harness/outbound');
  const oldReview = status.briefs[0].interrupt_id;
  await page.getByRole('button', { name: 'Edit unapproved brief' }).click();
  await page.getByLabel('Call purpose', { exact: true }).fill('Ask about next-week appointments');
  await page.getByRole('button', { name: 'Replace review' }).click();
  await page.getByRole('button', { name: 'Send brief to Inbox' }).waitFor();
  status = await request('/api/harness/outbound');
  assert.notEqual(status.briefs[0].interrupt_id, oldReview);
  const review = await request('/api/harness/interrupts/' + status.briefs[0].interrupt_id);
  await request('/api/harness/interrupts/' + review.id, 'POST', {
    nonce: review.nonce,
    action_digest: review.action_digest,
    status: 'resolved',
    payload: { approved: true },
  });
  await page.getByRole('button', { name: 'End outbound call' }).waitFor();
  const lookup = await request('/preview/delegate', 'POST', {
    text: 'Is the reviewed appointment window free?',
    id: 'browser-read',
  });
  await request('/preview/delegate', 'POST', { text: 'Replay this read', id: 'browser-read' });
  assert.equal(lookup.lookup_calls.length, 1);
  assert.deepEqual(lookup.lookup_calls[0], {
    method: 'tools/call',
    name: 'free_busy',
    arguments: { start: '2026-10-08T20:00:00.000Z', end: '2026-10-08T21:00:00.000Z' },
  });
  assert(lookup.spoken[0].includes('free') && lookup.spoken[0].includes('not a reservation'));
  const checkedBrief = (await request('/api/harness/outbound')).briefs[0];
  assert.equal(checkedBrief.lookup_receipt_ids.length, 1);
  assert(checkedBrief.brief.availability_checks[0].source_digest);

  await page.getByLabel('Owner note', { exact: true }).fill('Afternoon slots only');
  await page.getByRole('button', { name: 'Send note' }).click();
  await page.getByRole('button', { name: 'End outbound call' }).click();
  await page.getByRole('button', { name: 'End outbound call' }).waitFor({ state: 'hidden' });
  const preview = await request('/preview/state');
  assert.deepEqual(
    preview.observed.map((item) => item.action),
    ['create', 'session.instructions.append', 'hangup'],
  );
  assert(preview.status.briefs[0].receipt_id && preview.status.briefs[0].transcript_artifact_id);
  const interpretation = page.getByRole('region', { name: 'Call interpretation' }).first();
  await interpretation.getByRole('button', { name: 'Review interpretation in Inbox' }).waitFor();
  await interpretation.getByText('Callee quote', { exact: false }).waitFor();
  assert((await interpretation.textContent()).includes('no booking or follow-up action'));
  await interpretation.getByRole('button', { name: 'Review interpretation in Inbox' }).click();
  await interpretation.getByRole('button', { name: 'Review waiting in Inbox' }).waitFor();
  status = await request('/api/harness/outbound');
  const reportRow = status.briefs.find((row) => row.id === preview.status.briefs[0].id);
  const reportReview = await request(
    '/api/harness/interrupts/' + reportRow.outcome_report.review_interrupt_id,
  );
  assert.equal(reportReview.reason, 'outbound_report');
  assert.equal(reportReview.preview.interpretation.independently_verified, false);
  await request('/api/harness/interrupts/' + reportReview.id, 'POST', {
    nonce: reportReview.nonce,
    action_digest: reportReview.action_digest,
    status: 'resolved',
    payload: { approved: true },
  });
  await interpretation.getByText('Owner accepted', { exact: false }).waitFor();
  const assessed = (await request('/api/harness/outbound')).briefs.find(
    (row) => row.id === reportRow.id,
  );
  assert.equal(assessed.receipt_id, reportRow.receipt_id);
  assert.equal(assessed.outcome_report.payload.independently_verified, false);
  assert(assessed.outcome_report.review_receipt_id);
  // Schedule through the actual Admin form; approval preserves the reviewed time without dialing early.
  await page.getByLabel('Call timing', { exact: true }).selectOption('at');
  const chosen = new Date('2026-10-04T17:00:00Z');
  const local = new Date(chosen.getTime() - chosen.getTimezoneOffset() * 60000)
    .toISOString()
    .slice(0, 16);
  await page.getByLabel('Call time (your local timezone)', { exact: true }).fill(local);
  for (const [label, value] of [
    ['Callee name', 'Scheduled preview'],
    ['Destination number', '+14155552671'],
    ['Call purpose', 'Ask about next-week appointments'],
    ['Your first name', 'Alex'],
    ['Acceptable outcomes', 'An afternoon slot next week'],
  ])
    await page.getByLabel(label, { exact: false }).fill(value);
  await page.getByRole('button', { name: 'Send brief to Inbox' }).click();
  await page
    .getByRole('button', { name: 'Cancel pending call to Scheduled preview', exact: true })
    .waitFor();
  status = await request('/api/harness/outbound');
  const planned = status.briefs.find((row) => row.brief.callee_name === 'Scheduled preview');
  assert.equal(planned.schedule.not_before, chosen.getTime() / 1000);
  const plannedReview = await request('/api/harness/interrupts/' + planned.interrupt_id);
  assert.deepEqual(plannedReview.preview.schedule, planned.schedule);
  await request('/api/harness/interrupts/' + plannedReview.id, 'POST', {
    nonce: plannedReview.nonce,
    action_digest: plannedReview.action_digest,
    status: 'resolved',
    payload: { approved: true },
  });
  for (let i = 0; i < 30; i++) {
    if (
      (await request('/preview/state')).status.briefs.find((row) => row.id === planned.id)
        ?.state === 'scheduled'
    )
      break;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  assert.equal(
    (await request('/preview/state')).observed.filter((row) => row.action === 'create').length,
    1,
  );
  await request('/preview/advance', 'POST', { at: planned.schedule.not_before });
  await page.getByRole('button', { name: 'End outbound call' }).waitFor();
  await page.getByRole('button', { name: 'End outbound call' }).click();
  await page.getByRole('button', { name: 'End outbound call' }).waitFor({ state: 'hidden' });
  const pending = await request('/api/harness/outbound/briefs', 'POST', {
    ...planned.brief,
    callee_name: 'Cancelled preview',
    schedule: { mode: 'at', at: '2026-10-04T18:00:00Z' },
  });
  await page
    .getByRole('button', { name: 'Cancel pending call to Cancelled preview', exact: true })
    .click();
  await page
    .getByRole('button', { name: 'Cancel pending call to Cancelled preview', exact: true })
    .waitFor({ state: 'hidden' });
  assert.equal(
    (await request('/api/harness/outbound')).briefs.find((row) => row.id === pending.id).state,
    'cancelled',
  );
  const phonePending = await request('/api/harness/outbound/briefs', 'POST', {
    ...planned.brief,
    callee_name: 'Phone cancellation preview',
    schedule: { mode: 'at', at: '2026-10-04T18:00:00Z' },
  });
  await mkdir(evidence, { recursive: true });
  await page.evaluate(() => scrollTo(0, 0));
  await page.screenshot({ path: `${evidence}/admin-desktop.png`, fullPage: true });
  await page.setViewportSize({ width: 412, height: 915 });
  await page.evaluate(() => scrollTo(0, 0));
  await page.waitForFunction(
    () => document.querySelector('aside').getBoundingClientRect().right <= 0,
  );
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  await page.screenshot({ path: `${evidence}/admin-mobile.png`, fullPage: true });
  await page
    .getByRole('region', { name: 'Call interpretation' })
    .first()
    .screenshot({ path: `${evidence}/admin-outcome.png` });
  assert.deepEqual(errors, []);
  const first = preview.status.briefs[0].brief;
  const phoneBrief = await request('/api/harness/outbound/briefs', 'POST', {
    ...first,
    purpose: 'Phone controls preview',
  });
  const phoneReview = await request('/api/harness/interrupts/' + phoneBrief.interrupt_id);
  await request('/api/harness/interrupts/' + phoneReview.id, 'POST', {
    nonce: phoneReview.nonce,
    action_digest: phoneReview.action_digest,
    status: 'resolved',
    payload: { approved: true },
  });
  for (let i = 0; i < 30; i++) {
    if ((await request('/preview/state')).status.active) break;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  await page.evaluate(async () => {
    for (const link of document.querySelectorAll('link[rel="stylesheet"]')) link.remove();
    await new Promise((resolve, reject) => {
      const style = document.createElement('link');
      style.rel = 'stylesheet';
      style.href = '/phone/app.css';
      style.onload = resolve;
      style.onerror = reject;
      document.head.append(style);
    });
    const { calls } = await import('/phone/screens/calls.mjs');
    const headers = {
      'x-panini-console': 'panini-outbound-preview-only',
      'Content-Type': 'application/json',
    };
    const fabric = {
      request: async (method, path, body) => {
        if (path === '/calls') {
          const state = await (await fetch('/preview/state', { headers })).json();
          return {
            status: 200,
            body: {
              active: state.status.active,
              calls: state.status.briefs
                .filter((row) => row.state === 'ended')
                .map((row) => ({
                  id: row.id,
                  state: row.state,
                  direction: 'outbound',
                  duration_s: row.outcome.duration_s,
                  receipt_id: row.receipt_id,
                  summary: row.outcome.summary,
                  outcome_report: row.outcome_report,
                  lookup_receipt_ids: row.lookup_receipt_ids,
                })),
            },
          };
        }
        const target = path.startsWith('/calls/') ? path.replace('/calls/', '/outbound/') : path;
        const response = await fetch('/api/harness' + target, {
          method,
          headers,
          ...(body ? { body: JSON.stringify(body) } : {}),
        });
        return { status: response.status, body: await response.json() };
      },
    };
    const app = {
      fabric,
      viewRevision: 1,
      show(view) {
        window.previewShown = view;
      },
    };
    document.body.replaceChildren(await calls(app));
  });
  const phoneReport = page
    .getByRole('region', { name: 'Call interpretation' })
    .filter({
      has: page.getByRole('button', { name: 'Review interpretation in Inbox', exact: true }),
    })
    .first();
  await phoneReport
    .getByRole('button', { name: 'Review interpretation in Inbox', exact: true })
    .click();
  await page.waitForFunction(() => window.previewShown === 'inbox');
  const phoneReportRow = (await request('/api/harness/outbound')).briefs.find(
    (row) => row.id === planned.id,
  );
  assert(phoneReportRow.outcome_report.review_interrupt_id);
  assert.equal(phoneReportRow.outcome_report.state, 'ready');
  await page.getByLabel('Owner call note').fill('Keep the original brief');
  await page.getByRole('button', { name: 'Send owner note' }).click();
  await page.waitForFunction(() => document.querySelector('.outbound-note').value === '');
  await page.screenshot({ path: `${evidence}/android-calls.png`, fullPage: true });
  await page
    .getByRole('button', { name: 'Cancel pending call to Phone cancellation preview', exact: true })
    .click();
  await page
    .getByRole('button', { name: 'Cancel pending call to Phone cancellation preview', exact: true })
    .waitFor({ state: 'hidden' });
  assert.equal(
    (await request('/api/harness/outbound')).briefs.find((row) => row.id === phonePending.id).state,
    'cancelled',
  );
  await page.getByRole('button', { name: 'Transfer to my phone' }).click();
  await page.getByRole('heading', { name: 'No active call' }).waitFor();
  assert.deepEqual(errors, []);
  console.log(
    'PASS real setup, credential clearing, draft/edit replacement, exact review approval, chosen time, no early dispatch, scheduled fake SIP dispatch, PC/PWA cancellation, fake SIP create/disclosure/note/hangup, transcript and receipt; desktop/mobile fit; quote-cited report, PC owner assessment and phone restore-before-review; reviewed availability form, exact granted free/busy query and no replay; real phone screen note/transfer through a fake fabric adapter. No real call or physical device.',
  );
} catch (error) {
  console.error(JSON.stringify(await request('/preview/state')));
  if (browser)
    await browser
      .contexts()[0]
      .pages()[0]
      .screenshot({ path: `${evidence}/admin-error.png`, fullPage: true });
  throw error;
} finally {
  await browser?.close();
  server.kill();
  if (server.exitCode === null) await new Promise((resolve) => server.once('exit', resolve));
}
