import test from 'node:test';
import assert from 'node:assert/strict';
import { rememberClosed, notificationPath } from '../lib/notifications.mjs';

test('notification tombstones outlive push TTL and stay bounded', () => {
  const now = 100000000;
  const retained = rememberClosed({ old: 0, recent: now - 1000 }, 'resolved', now);
  assert.deepEqual(retained, { recent: now - 1000, resolved: now });
  const many = Object.fromEntries(Array.from({ length: 1000 }, (_, index) => [`id${index}`, now]));
  assert.equal(Object.keys(rememberClosed(many, 'new', now)).length, 1000);
});

test('deep links open exact thread and never accept injected URLs', () => {
  assert.equal(notificationPath({ type: 'interrupt', thread_id: 'thread' }), '/?tab=inbox');
  assert.equal(
    notificationPath({ type: 'continuity', thread_id: 'thread_1' }),
    '/?tab=conversation&thread=thread_1',
  );
  assert.equal(
    notificationPath({ type: 'continuity', thread_id: 'https://evil.test' }),
    '/?tab=inbox',
  );
});
