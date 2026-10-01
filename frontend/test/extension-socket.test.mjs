import { test } from 'node:test';
import assert from 'node:assert/strict';
import { once } from 'node:events';
import { createServer } from 'node:http';
import { WebSocket, WebSocketServer } from 'ws';
import { attachExtensionSocket } from '../server/extension-socket.mjs';

test('extension relay forwards initial frames and rejects web origins and hostile hosts', async () => {
  const backend = createServer();
  const upstream = new WebSocketServer({ server: backend });
  upstream.on('connection', (ws) =>
    ws.on('message', (data) => {
      if (data.toString() === 'reject') ws.close(1008, 'Another Jev extension is connected');
      else ws.send(data);
    }),
  );
  backend.listen(0, '127.0.0.1');
  await once(backend, 'listening');
  const server = createServer();
  const close = attachExtensionSocket(server, {
    backendUrl: `http://127.0.0.1:${backend.address().port}`,
  });
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  const url = `ws://127.0.0.1:${server.address().port}/ws/extension`;
  try {
    const socket = new WebSocket(url, { origin: 'chrome-extension://testextension' });
    await once(socket, 'open');
    const received = once(socket, 'message');
    socket.send(JSON.stringify({ type: 'hello', tab: 42 }));
    assert.equal(JSON.parse((await received)[0].toString()).tab, 42);
    socket.send('reject');
    const [code, reason] = await once(socket, 'close');
    assert.equal(code, 1008);
    assert.match(reason.toString(), /Another Jev extension/);
    for (const options of [
      { origin: 'https://attacker.example' },
      { headers: { host: 'attacker.example' }, origin: 'chrome-extension://testextension' },
    ]) {
      const denied = new WebSocket(url, options);
      await assert.rejects(once(denied, 'open'), /403/);
    }
  } finally {
    close();
    await new Promise((resolve) => server.close(resolve));
    for (const client of upstream.clients) client.terminate();
    await new Promise((resolve) => upstream.close(resolve));
    await new Promise((resolve) => backend.close(resolve));
  }
});
