// Phone app card logic (V2-15 §5.3): buttons per level, decisions, restore-before-decide, and the exact statement
// the phone signs (it must match backend/fabric/router.py: decision_statement).
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { canonical, card, decision, decisionStatement, sha256Hex, stale } from '../lib/cards.mjs';
import { parsePairing, socketUrl } from '../lib/fabric.mjs';

const approval = {
  id: 'i1',
  kind: 'approval',
  level: 'tap',
  status: 'open',
  nonce: 'n1',
  action_digest: 'd1',
  title: 'Send email',
};

test('cards offer what the level allows', () => {
  assert.deepEqual(card(approval).buttons, ['approve', 'decline']);
  assert.deepEqual(card({ ...approval, level: 'local' }).buttons, []);
  assert.match(card({ ...approval, level: 'local' }).note, /on your PC/);
  assert.deepEqual(card({ ...approval, level: 'verify' }).buttons, ['approve', 'decline']);
  assert.match(card({ ...approval, level: 'verify' }).note, /passkey/);
  assert.deepEqual(card({ ...approval, kind: 'takeover' }).buttons, ['done', 'decline']);
  assert.equal(card({ ...approval, kind: 'question' }).asks, true);
  assert.deepEqual(card({ ...approval, status: 'resolved' }).buttons, []);
});

test('buttons map to decisions that fit the response schemas', () => {
  assert.deepEqual(decision(approval, 'approve'), {
    status: 'resolved',
    payload: { approved: true },
  });
  assert.deepEqual(decision(approval, 'decline'), {
    status: 'resolved',
    payload: { approved: false },
  });
  assert.deepEqual(decision({ kind: 'question' }, 'answer', 'Tuesday'), {
    status: 'resolved',
    payload: { answer: 'Tuesday' },
  });
  assert.deepEqual(decision({ kind: 'takeover' }, 'decline'), {
    status: 'cancelled',
    payload: null,
  });
});

test('restore before deciding refuses changed, answered or missing requests', () => {
  assert.equal(stale(approval, approval), '');
  assert.match(stale(approval, null), /no longer available/);
  assert.match(stale(approval, { ...approval, status: 'resolved' }), /already answered/);
  assert.match(stale(approval, { ...approval, nonce: 'n2' }), /changed/);
  assert.match(stale(approval, { ...approval, action_digest: 'other' }), /changed/);
});

test('canonical JSON and the signed statement match the PC', async () => {
  const backslash = String.fromCharCode(92); // The PC escapes non-ASCII (ensure_ascii).
  assert.equal(
    canonical({ b: 1, a: [true, null, 'é'] }),
    `{"a":[true,null,"${backslash}u00e9"],"b":1}`,
  );
  // Python: hashlib.sha256(json.dumps({"approved": True}, sort_keys=True, separators=(",", ":")).encode())
  const digest = await sha256Hex('{"approved":true}');
  assert.equal(
    await decisionStatement('i1', 'n1', 'd1', 'resolved', { approved: true }),
    `i1|n1|d1|resolved|${digest}`,
  );
  assert.equal(
    await decisionStatement('i1', 'n1', null, 'cancelled', null),
    `i1|n1||cancelled|${await sha256Hex('{}')}`,
  );
});

test('pairing links and socket addresses', () => {
  const link = parsePairing('#v=1&pc=abc&ks=KS&ke=KE&c=ABCD2345&exp=99&ch=chan1');
  assert.deepEqual(link.pc, { id: 'abc', signPub: 'KS', agreePub: 'KE' });
  assert.equal(link.code, 'ABCD2345');
  assert.equal(parsePairing('#v=2&pc=a'), null);
  assert.equal(socketUrl('http://192.168.1.5:3211', ''), 'ws://192.168.1.5:3211/fabric');
  assert.equal(socketUrl('https://relay.example/app/', 'chan1'), 'wss://relay.example/d/chan1');
});
