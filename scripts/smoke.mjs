/** Explicit live-provider smoke test; calls may consume API credits. */
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { parseArgs } from 'node:util';
const { values } = parseArgs({ options: { 'all-examples': { type: 'boolean', default: false } } });
const base = process.env.APP_URL || 'http://localhost:3000';
const catalog = JSON.parse(readFileSync(new URL('../backend/tasks.json', import.meta.url), 'utf8'));
const definitions = new Map(catalog.experiments.map((task) => [task.id, task]));
const reportPath = new URL(
  values['all-examples']
    ? '../artifacts/live-all-examples.json'
    : '../artifacts/live-comparisons.json',
  import.meta.url,
);
const startedAt = new Date().toISOString();
const checks = [];
const save = () => {
  mkdirSync(new URL('../artifacts/', import.meta.url), { recursive: true });
  writeFileSync(
    reportPath,
    JSON.stringify({ base, startedAt, updatedAt: new Date().toISOString(), checks }, null, 2),
  );
};
const record = (check) => {
  checks.push(check);
  save();
  console.log(JSON.stringify(check));
};
const request = async (path, body) => {
  const response = await fetch(base + path, {
    ...(body
      ? {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify(body),
        }
      : {}),
    signal: AbortSignal.timeout(70000),
  });
  if (!response.ok)
    throw new Error(`${path}: HTTP ${response.status}: ${(await response.text()).slice(0, 300)}`);
  return response.json();
};
const health = await request('/api/health');
record({ check: 'health', ok: health.status === 'ok' });
const { tasks } = await request('/api/tasks');
record({
  check: 'task-catalog',
  ok:
    tasks.length === definitions.size &&
    new Set(tasks.map((task) => task.id)).size === definitions.size &&
    tasks.every(
      (task) =>
        JSON.stringify(task.examples) === JSON.stringify(definitions.get(task.id)?.examples),
    ),
});
for (const task of tasks) {
  const examples = values['all-examples'] ? task.examples : task.examples.slice(0, 1);
  for (const [index, text] of examples.entries()) {
    try {
      const result = await request('/api/compare', { task: task.id, text });
      const summarize = (provider) => {
        const questionKeys = Object.keys(definitions.get(task.id)?.questions || {});
        const answers = provider?.answers || {};
        const valid =
          !provider?.error &&
          questionKeys.length > 0 &&
          questionKeys.every(
            (key) =>
              answers[key]?.type === 'boolean' &&
              typeof answers[key].probability === 'number' &&
              Number.isFinite(answers[key].probability) &&
              answers[key].probability >= 0 &&
              answers[key].probability <= 1,
          );
        return {
          valid,
          result: provider?.result,
          answers,
          latency_ms: provider?.latency_ms,
          tokens: provider?.tokens,
          cost: provider?.cost,
          error: provider?.error,
          generation_id: provider?.generation_id,
        };
      };
      const direct = summarize(result.direct);
      const jev = summarize(result.jev);
      record({
        check: task.id,
        example: index + 1,
        ok:
          direct.valid &&
          jev.valid &&
          result.jev.is_real === true &&
          typeof jev.generation_id === 'string' &&
          jev.generation_id.length > 0,
        direct,
        jev,
      });
    } catch (error) {
      record({ check: task.id, example: index + 1, ok: false, error: error.message });
    }
  }
}
if (checks.some((check) => !check.ok)) process.exitCode = 1;
