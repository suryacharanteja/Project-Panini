import assert from 'node:assert/strict';
import test from 'node:test';
import { Socket } from '../lib/socket.mjs';
class FakeSocket {
  constructor() {
    this.readyState = 0;
    FakeSocket.last = this;
    queueMicrotask(() => {
      this.readyState = 1;
      this.onopen();
    });
  }
  send() {}
  close() {
    this.readyState = 3;
    this.onclose?.();
  }
}
test('malformed reply rejects pending request and closes socket', async () => {
  const socket = new Socket('ws://localhost', { WebSocketClass: FakeSocket });
  await socket.open();
  const reply = socket.send({ type: 'hello' });
  FakeSocket.last.onmessage({ data: '{broken' });
  await assert.rejects(reply, /Invalid response/);
  assert.equal(FakeSocket.last.readyState, 3);
});
test('request timeout closes connection so late replies cannot be misattributed', async () => {
  const socket = new Socket('ws://localhost', { WebSocketClass: FakeSocket, timeoutMs: 10 });
  await socket.open();
  await assert.rejects(socket.send({ type: 'env' }), /timed out/);
  assert.equal(FakeSocket.last.readyState, 3);
});
test('disconnect rejects every pending request', async () => {
  const socket = new Socket('ws://localhost', { WebSocketClass: FakeSocket });
  await socket.open();
  const a = socket.send({ a: 1 });
  const b = socket.send({ b: 2 });
  socket.close();
  await assert.rejects(a, /closed/);
  await assert.rejects(b, /closed/);
});
import { Fabric } from '../lib/fabric.mjs';
test('concurrent encrypted requests preserve send/open ordering', async () => {
  const fabric = new Fabric({}, {});
  let active = 0,
    maximum = 0;
  fabric.perform = async () => {
    active++;
    maximum = Math.max(maximum, active);
    await new Promise((r) => setTimeout(r, 5));
    active--;
    return {};
  };
  await Promise.all([fabric.request('GET', '/a'), fabric.request('GET', '/b')]);
  assert.equal(maximum, 1);
});
test('a mutation with an ambiguous reply is never automatically replayed', async () => {
  const fabric = new Fabric({}, {});
  let sends = 0;
  fabric.session = { seal: async () => ({}) };
  fabric.socket = {
    send: async () => {
      sends++;
      throw new Error('lost reply');
    },
    close() {},
  };
  await assert.rejects(fabric.request('POST', '/ask', { text: 'hello' }), /lost reply/);
  assert.equal(sends, 1);
});
