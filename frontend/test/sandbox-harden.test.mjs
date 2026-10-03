import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createHmac } from 'node:crypto';
import { hardenSupervisorEnvironment } from '../../deployment/computers/harden-supervisor.mjs';

// Ported from OpenDots tests/computer-deployment.test.js (V2-11).
const upstream = `export function environmentFor(botId, env) {
  const computerToken = env.COMPUTER_TOKEN?.trim() || undefined;
  return [\`COMPUTER_BOT_ID=\${botId}\`, \`COMPUTER_TOKEN=\${computerToken}\`];
}`;

test('each sandbox computer gets its own credential and never the master', async () => {
  const module = await import(
    `data:text/javascript,${encodeURIComponent(hardenSupervisorEnvironment(upstream))}`
  );
  const master = 'fixture-master-only-for-this-test';
  const first = module.environmentFor('panini', { COMPUTER_TOKEN: master });
  const second = module.environmentFor('research', { COMPUTER_TOKEN: master });
  assert.equal(
    first[1],
    `COMPUTER_TOKEN=${createHmac('sha256', master).update('panini-computer:panini').digest('hex')}`,
  );
  assert.notEqual(first[1], second[1]);
  assert.ok(!first.join().includes(master));
  assert.throws(() => module.environmentFor('panini', { COMPUTER_TOKEN: 'short' }));
});

test('the patch refuses a changed or ambiguous upstream', () => {
  assert.throws(() => hardenSupervisorEnvironment('changed upstream'), /contract changed/);
  assert.throws(() => hardenSupervisorEnvironment(upstream + upstream), /contract changed/);
});
