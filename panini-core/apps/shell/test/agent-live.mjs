// Live check of the PANINI agent through the real app: the planner routes an open-ended
// request to the agent, which remembers a preference, researches with Search and
// finishes with verification evidence. Uses the real model and search (costs a few cents).
import { _electron as electron } from 'playwright';
import { createRequire } from 'node:module';
import { resolve } from 'node:path';

const require = createRequire(import.meta.url);
const root = resolve(import.meta.dirname, '../..');
process.loadEnvFile(resolve(root, '.env'));
const env = { ...process.env, PANINI_NODE: process.execPath };
delete env.ELECTRON_RUN_AS_NODE;

const application = await electron.launch({
  executablePath: require('electron'),
  args: [resolve(root, 'panini-core/apps/shell')],
  env,
  timeout: 60000,
});
const checks = [];
try {
  const page = await application.firstWindow();
  let current;
  for (let attempt = 0; attempt < 150; attempt++) {
    current = await page.evaluate(() => window.panini.invoke('status'));
    if (current.connected && current.snapshot?.state === 'ready') break;
    await new Promise((done) => setTimeout(done, 500));
  }
  if (current?.snapshot?.state !== 'ready') throw new Error('Runtime did not become ready');
  const caps = await page.evaluate(() => window.panini.invoke('capabilities'));
  const agent = caps.capabilities.find((c) => c.capability === 'agent');
  if (agent?.state !== 'ready') throw new Error(`Agent capability: ${JSON.stringify(agent)}`);
  checks.push('agent capability ready');

  const marker = `panini-live-${Date.now()}`;
  await page.evaluate(
    (text) => window.panini.invoke('turn', { text }),
    `Remember this about me: I prefer concise answers (${marker}). Then research the current ` +
      'stable (LTS) version of Node.js and tell me it with the source.',
  );
  let task;
  for (let attempt = 0; attempt < 480; attempt++) {
    const value = await page.evaluate(() => window.panini.invoke('status'));
    task = value.snapshot?.task;
    if (['completed', 'blocked', 'failed', 'stopped', 'ready', 'needs_input'].includes(task?.state))
      break;
    await new Promise((done) => setTimeout(done, 500));
  }
  console.log(
    JSON.stringify(
      {
        capability: task?.capability,
        state: task?.state,
        outcome: task?.outcome,
        message: task?.message,
      },
      null,
      2,
    ),
  );
  if (task?.outcome !== 'verified_complete') throw new Error('Agent task did not verify');
  const steps = (task.steps || []).map((s) => `${s.capability}:${s.state}`);
  if (!steps.some((s) => s.startsWith('agent:'))) throw new Error(`No agent step: ${steps}`);
  checks.push(`planner steps ${steps.join(', ')}; outcome verified_complete`);
  // The answer is either the agent's text or, when the planner added a Search step,
  // the source cards shown in the avatar.
  const status = await page.evaluate(() => window.panini.invoke('status'));
  const sources = (status.snapshot.results || []).map((r) => r.url || '');
  if (
    !/node/i.test(task.message) &&
    !sources.some((url) => /nodejs|node\.js|github\.com\/nodejs/i.test(url))
  )
    throw new Error(`No Node.js answer or source: ${task.message} ${sources}`);
  checks.push('Node.js answer or official sources delivered');
  checks.push(`conversation still ${status.snapshot.state}`);
  console.log(JSON.stringify({ checks, marker }));
} finally {
  await application.close();
}

