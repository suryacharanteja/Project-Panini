import assert from 'node:assert/strict';
import { mkdir } from 'node:fs/promises';
import { chromium } from 'playwright';

const origin = 'http://127.0.0.1:3223';
const headers = {
  'x-panini-console': 'panini-spaces-development-only-token',
  'content-type': 'application/json',
};
async function api(path, body, method = body ? 'POST' : 'GET') {
  const response = await fetch(`${origin}/api/harness/spaces${path}`, {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
  });
  assert.ok(response.ok, `API ${path}: ${response.status}`);
  return response.json();
}
const space = await api('', { name: 'Notebook qualification' });
const doc = await api('/pages', {
  space_id: space.id,
  title: 'Launch plan',
  content: '# Mission\n\nA durable notebook.',
});
const browser = await chromium.launch({ headless: true, channel: 'chrome' });
try {
  const page = await browser.newPage({
    extraHTTPHeaders: headers,
    viewport: { width: 1440, height: 1000 },
  });
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.goto(`${origin}/spaces`);
  await page.getByLabel('Space', { exact: true }).selectOption(space.id);
  await page.getByRole('button', { name: 'Launch plan', exact: true }).click();
  const scripted = await fetch(`${origin}/api/harness/spaces-dev/draft/${doc.id}`, {
    method: 'POST',
    headers,
  });
  assert.ok(scripted.ok);
  await page.getByRole('button', { name: 'Discuss page', exact: true }).click();
  await page.getByLabel('Message PANINI').fill('Propose an edit for my review.');
  await page.getByRole('button', { name: 'Send', exact: true }).click();
  await page
    .getByLabel('Review page draft', { exact: true })
    .fill('# Owner reviewed\n\nEdited before approval.');
  await page.getByRole('button', { name: 'Approve', exact: true }).click();
  await page.getByText('Saved the approved page.', { exact: true }).waitFor();
  assert.equal(
    (await api(`/pages/${doc.id}`)).content,
    '# Owner reviewed\n\nEdited before approval.',
  );
  // Reopen after the agent's write, so the editor starts from the new server revision.
  await page.getByLabel('Space', { exact: true }).selectOption('');
  await page.getByLabel('Space', { exact: true }).selectOption(space.id);
  await page.getByRole('button', { name: 'Launch plan', exact: true }).click();
  await page.getByRole('button', { name: 'Markdown', exact: true }).click();
  const content = page.getByLabel('Markdown content');
  await content.fill('# Mission\n\n- [ ] Launch\n\n| A | B |\n| --- | --- |\n| 1 | 2 |');
  await page.getByRole('button', { name: 'Save', exact: true }).click();
  await page.waitForFunction(() =>
    [...document.querySelectorAll('[role="status"]')].some((e) => e.textContent === 'saved'),
  );
  let stored = await api(`/pages/${doc.id}`);
  assert.match(stored.content, /Launch/);
  await page.getByRole('button', { name: 'Rich text', exact: true }).click();
  await page.locator('.tiptap table').waitFor();
  assert.equal(await page.locator('.tiptap input[type="checkbox"]').count(), 1);
  await page.getByRole('button', { name: 'Markdown', exact: true }).click();

  await page.route('**/api/harness/spaces/pages/*', (route) =>
    route.request().method() === 'PATCH' ? route.abort() : route.continue(),
  );
  await content.fill('Preserve this offline draft');
  await page.getByRole('button', { name: 'Save', exact: true }).click();
  await page.waitForFunction(() =>
    [...document.querySelectorAll('[role="status"]')].some((e) => e.textContent === 'failed'),
  );
  await page.reload();
  await page.getByLabel('Space', { exact: true }).selectOption(space.id);
  await page.getByRole('button', { name: 'Launch plan', exact: true }).click();
  await page.getByRole('button', { name: 'Markdown', exact: true }).click();
  assert.equal(await content.inputValue(), 'Preserve this offline draft');
  await page.unroute('**/api/harness/spaces/pages/*');

  stored = await api(`/pages/${doc.id}`);
  await api(
    `/pages/${doc.id}`,
    { expected_revision: stored.revision, content: 'Concurrent server edit' },
    'PATCH',
  );
  await content.fill('My conflicting draft');
  await page.getByRole('button', { name: 'Save', exact: true }).click();
  await page.waitForFunction(() =>
    [...document.querySelectorAll('[role="status"]')].some((e) => e.textContent === 'conflict'),
  );
  page.once('dialog', (dialog) => dialog.dismiss());
  await page.getByRole('button', { name: 'Review conflict', exact: true }).click();
  await page.getByRole('button', { name: 'Save', exact: true }).click();
  await page.waitForFunction(() =>
    [...document.querySelectorAll('[role="status"]')].some((e) => e.textContent === 'conflict'),
  );
  assert.equal((await api(`/pages/${doc.id}`)).content, 'Concurrent server edit');
  page.once('dialog', (dialog) => dialog.accept());
  await page.getByRole('button', { name: 'Review conflict', exact: true }).click();
  await page.waitForFunction(() =>
    [...document.querySelectorAll('[role="status"]')].some((e) => e.textContent === 'saved'),
  );
  assert.equal((await api(`/pages/${doc.id}`)).content, 'My conflicting draft');
  // A recovered draft with an old revision must also remain unable to overwrite the server.
  await page.route('**/api/harness/spaces/pages/*', (route) =>
    route.request().method() === 'PATCH' ? route.abort() : route.continue(),
  );
  await content.fill('Recovered conflict');
  await page.getByRole('button', { name: 'Save', exact: true }).click();
  await page.waitForFunction(() =>
    [...document.querySelectorAll('[role="status"]')].some((e) => e.textContent === 'failed'),
  );
  stored = await api(`/pages/${doc.id}`);
  await api(
    `/pages/${doc.id}`,
    { expected_revision: stored.revision, content: 'Server changed while editor was closed' },
    'PATCH',
  );
  await page.reload();
  await page.getByLabel('Space', { exact: true }).selectOption(space.id);
  await page.getByRole('button', { name: 'Launch plan', exact: true }).click();
  await page.getByRole('button', { name: 'Markdown', exact: true }).click();
  await page.unroute('**/api/harness/spaces/pages/*');
  await page.getByRole('button', { name: 'Save', exact: true }).click();
  await page.waitForFunction(() =>
    [...document.querySelectorAll('[role="status"]')].some((e) => e.textContent === 'conflict'),
  );
  assert.equal((await api(`/pages/${doc.id}`)).content, 'Server changed while editor was closed');
  page.once('dialog', (dialog) => dialog.accept());
  await page.getByRole('button', { name: 'Review conflict', exact: true }).click();
  await page.waitForFunction(() =>
    [...document.querySelectorAll('[role="status"]')].some((e) => e.textContent === 'saved'),
  );
  page.once('dialog', (dialog) => dialog.accept('Nested note'));
  await page.getByRole('button', { name: 'New subpage', exact: true }).click();
  await page.getByLabel('Page title').waitFor();
  await page.getByRole('button', { name: 'Nested note', exact: true }).waitFor();
  assert.equal(
    (await api(`/${space.id}/pages`)).pages.find((p) => p.title === 'Nested note').parent_id,
    doc.id,
  );
  await page.getByLabel('Search pages').fill('Nested');
  await page.getByRole('button', { name: 'Nested note', exact: true }).waitFor();
  assert.equal(await page.getByRole('button', { name: 'Launch plan', exact: true }).count(), 0);
  await page.getByLabel('Search pages').fill('');
  page.once('dialog', (dialog) => dialog.accept());
  await page.getByRole('button', { name: 'Trash Nested note', exact: true }).click();
  await page.getByRole('button', { name: 'Trash', exact: true }).click();
  await page.getByRole('button', { name: 'Restore Nested note', exact: true }).click();
  await page.getByRole('button', { name: 'Active pages', exact: true }).click();
  await page.getByRole('button', { name: 'Nested note', exact: true }).waitFor();
  await page.getByRole('button', { name: 'Launch plan', exact: true }).click();
  await page.getByRole('button', { name: 'Markdown', exact: true }).click();
  await content.fill(
    '# Mission control\n\nResearch and decisions, kept together.\n\n## Launch checklist\n\n- [x] Review the plan\n- [ ] Qualify the release\n\n| Workstream | Status |\n| --- | --- |\n| Notebook | In development |\n| Red-team | Deferred |',
  );
  await page.getByRole('button', { name: 'Save', exact: true }).click();
  await page.waitForFunction(() =>
    [...document.querySelectorAll('[role="status"]')].some((e) => e.textContent === 'saved'),
  );
  await page.getByRole('button', { name: 'Rich text', exact: true }).click();
  assert.deepEqual(errors, []);
  await mkdir('artifacts/spaces', { recursive: true });
  await page.screenshot({ path: 'artifacts/spaces/admin-desktop.png', fullPage: true });
  await page.setViewportSize({ width: 412, height: 915 });
  await page.waitForFunction(
    () => document.querySelector('aside').getBoundingClientRect().right <= 1,
  );
  await page.waitForFunction(
    () =>
      document.querySelector('[aria-label="Page title"]').getBoundingClientRect().right <=
      window.innerWidth,
  );
  await page.screenshot({
    path: 'artifacts/spaces/admin-mobile.png',
    fullPage: true,
    animations: 'disabled',
  });
  assert.ok(
    await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
    'Mobile overflow',
  );
  console.log(
    'PASS: real page-conversation SSE review/owner-edited approval; rich/Markdown tasks/tables; autosave; failed draft reload; recovered and cancelled conflicts preserve server revision; confirmed replacement; nested creation; search; trash/restore; desktop/mobile; no runtime errors.',
  );
} finally {
  await browser.close();
}
