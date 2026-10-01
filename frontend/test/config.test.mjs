import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readConfig, parseOrigins } from '../server/config.mjs';
import { allowedRequest, createRateLimiter } from '../server/security.mjs';

test('configuration rejects ambiguous ports and unsafe origin values before startup', () => {
  assert.throws(() => readConfig({ FRONTEND_PORT: '8200' }), /distinct/);
  assert.throws(() => readConfig({ BACKEND_PORT: 'invalid' }), /BACKEND_PORT/);
  for (const origin of [
    'file:///tmp',
    'https://user:pass@example.com',
    'https://example.com/path',
    '*',
  ]) {
    assert.throws(() => parseOrigins(origin), /origin/i);
  }
  assert.deepEqual(parseOrigins(' http://localhost:3000/ '), ['http://localhost:3000']);
});

test('an explicit allowed origin works while unrelated cross-site origins are rejected', () => {
  const request = (origin) => ({
    headers: { host: 'localhost:3000', origin, 'sec-fetch-site': 'cross-site' },
  });
  assert.equal(allowedRequest(request('http://localhost:4000'), ['http://localhost:4000']), true);
  assert.equal(
    allowedRequest(request('https://untrusted.example'), ['http://localhost:4000']),
    false,
  );
});

test('rate limits reset only when the time window changes', () => {
  let time = 1000;
  const limiter = createRateLimiter(2, () => time);
  assert.equal(limiter.take(), true);
  assert.equal(limiter.take(), true);
  assert.equal(limiter.take(), false);
  assert.equal(limiter.retryAfter(), 59);
  time = 60000;
  assert.equal(limiter.take(), true);
});
