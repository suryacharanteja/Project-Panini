import { after, before, test } from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { once } from 'node:events';
import { Readable } from 'node:stream';
import { WebSocket } from 'ws';
import { createGateway } from '../server/gateway.mjs';
import { allowedRequest } from '../server/security.mjs';

let backend, gateway, base, backendUrl, disconnected;
test('extension navigation can open public documents without granting cross-site API access', () => {
  const request = {
    method: 'GET',
    path: '/browser-demo.html',
    headers: {
      host: 'localhost:3000',
      'sec-fetch-site': 'cross-site',
      'sec-fetch-mode': 'navigate',
      'sec-fetch-dest': 'document',
      origin: 'chrome-extension://test',
    },
  };
  assert.equal(allowedRequest(request), true);
  for (const path of ['/api/browser/stop', '/api/health', '/search/api/ask'])
    assert.equal(allowedRequest({ ...request, path }), false);
  assert.equal(allowedRequest({ ...request, method: 'POST' }), false);
  assert.equal(
    allowedRequest({ ...request, headers: { ...request.headers, 'sec-fetch-dest': 'iframe' } }),
    false,
  );
  assert.equal(
    allowedRequest({ ...request, headers: { ...request.headers, 'sec-fetch-mode': 'cors' } }),
    false,
  );
  assert.equal(
    allowedRequest({ ...request, headers: { ...request.headers, host: 'evil.example' } }),
    false,
  );
});
test('rejects DNS rebinding even when Origin matches the hostile Host', () => {
  assert.equal(
    allowedRequest({
      headers: { host: 'attacker.example:3000', origin: 'http://attacker.example:3000' },
    }),
    false,
  );
});
before(async () => {
  backend = createServer(async (req, res) => {
    if (req.url === '/api/browser/run-stream') {
      res.writeHead(200, { 'content-type': 'application/x-ndjson' });
      res.write('{"event":"initialized"}\n');
      req.socket.once('close', () => disconnected?.());
      return;
    }
    let body = '';
    try {
      for await (const part of req) body += part;
    } catch {
      res.destroy();
      return;
    }
    if (req.url === '/api/compare') {
      await new Promise((resolve) => setTimeout(resolve, 30));
      res.setHeader('content-type', 'application/json');
      res.end(JSON.stringify({ direct: { result: JSON.parse(body).text }, jev: { result: 'ok' } }));
      return;
    }
    res.setHeader('content-type', 'application/json');
    res.end(
      JSON.stringify({
        status: 'ok',
        received: body,
        origin: req.headers.origin,
        requestId: req.headers['x-request-id'],
      }),
    );
  }).listen(0, '127.0.0.1');
  await once(backend, 'listening');
  backendUrl = `http://127.0.0.1:${backend.address().port}`;
  gateway = createGateway({
    backendUrl,
    searchFetch: async (request) =>
      Response.json({
        path: new URL(request.url).pathname,
        query: new URL(request.url).search,
        origin: request.headers.get('origin'),
      }),
  });
  gateway.server.listen(0, '127.0.0.1');
  await once(gateway.server, 'listening');
  base = `http://127.0.0.1:${gateway.server.address().port}`;
});
after(async () => {
  await gateway.close();
  backend.closeAllConnections();
  await new Promise((resolve) => backend.close(resolve));
});

test('health checks dependencies and search paths keep the mount and query', async () => {
  const health = await fetch(`${base}/api/health`);
  assert.equal(health.status, 200);
  assert.equal((await health.json()).providers.search_configured, false);
  const res = await fetch(`${base}/search/results?q=test`);
  assert.deepEqual(await res.json(), {
    path: '/search/results',
    query: '?q=test',
    origin: 'http://127.0.0.1',
  });
  const old = await fetch(`${base}/search?q=test`, { redirect: 'manual' });
  assert.equal(old.headers.get('location'), '/search/results?q=test');
});

test('rejects foreign origins before proxying and accepts same-origin requests', async () => {
  assert.equal(
    (
      await fetch(`${base}/api/browser/stop`, {
        method: 'POST',
        headers: { origin: 'https://evil.example' },
      })
    ).status,
    403,
  );
  const res = await fetch(`${base}/api/browser/stop`, {
    method: 'POST',
    headers: { origin: base },
  });
  assert.equal(res.status, 200);
  assert.equal((await res.json()).origin, backendUrl);
});

test('multipart uploads are forwarded without consuming or changing their bytes', async () => {
  const form = new FormData();
  form.set('file', new Blob(['test audio']), 'test.webm');
  const res = await fetch(`${base}/api/stt/transcribe`, { method: 'POST', body: form });
  assert.match((await res.json()).received, /test audio/);
});

test('chunked oversized uploads receive 413 rather than a proxy failure', async () => {
  const body = Readable.from(
    (async function* () {
      for (let i = 0; i < 16; i++) yield Buffer.alloc(1024 * 1024, 'x');
    })(),
  );
  const response = await fetch(`${base}/api/stt/transcribe`, {
    method: 'POST',
    headers: { 'content-type': 'application/octet-stream' },
    body,
    duplex: 'half',
  });
  assert.equal(response.status, 413);
  assert.match((await response.json()).error, /too large/);
});

test('request IDs survive the gateway and API responses are not cached', async () => {
  const response = await fetch(`${base}/api/tasks`);
  const id = response.headers.get('x-request-id');
  assert.match(id, /^[a-f0-9-]{36}$/);
  assert.equal((await response.json()).requestId, id);
  assert.equal(response.headers.get('cache-control'), 'no-store');
});

test('search capacity recovers after a streaming client disconnects', async () => {
  const instance = createGateway({
    backendUrl,
    searchLimit: 1,
    searchFetch: async () =>
      new Response(
        new ReadableStream({
          start(controller) {
            controller.enqueue(new TextEncoder().encode('{"type":"intent"}\n'));
          },
        }),
      ),
  });
  instance.server.listen(0, '127.0.0.1');
  await once(instance.server, 'listening');
  const url = `http://127.0.0.1:${instance.server.address().port}/search/api/ask`;
  const abort = new AbortController();
  try {
    const first = await fetch(url, { method: 'POST', signal: abort.signal });
    await first.body.getReader().read();
    assert.equal((await fetch(url, { method: 'POST' })).status, 429);
    abort.abort();
    // Wait for server-side disconnect accounting, bounded by the test deadline.
    let health;
    for (let attempt = 0; attempt < 20; attempt++) {
      health = await (await fetch(url.replace('/search/api/ask', '/api/health'))).json();
      if (health.work.searches.active === 0) break;
      await new Promise((resolve) => setTimeout(resolve, 10));
    }
    assert.equal(health.work.searches.active, 0);
    const next = await fetch(url, { method: 'POST' });
    assert.equal(next.status, 200);
    await next.body.cancel();
  } finally {
    abort.abort();
    await instance.close();
  }
});

test('closing a continuous NDJSON stream cancels the upstream browser request', async () => {
  const closed = new Promise((resolve) => {
    disconnected = resolve;
  });
  const controller = new AbortController();
  const res = await fetch(`${base}/api/browser/run-stream`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: '{"run_policy":"continuous"}',
    signal: controller.signal,
  });
  const reader = res.body.getReader();
  assert.match(new TextDecoder().decode((await reader.read()).value), /initialized/);
  controller.abort();
  await Promise.race([
    closed,
    new Promise((_, reject) =>
      setTimeout(() => reject(new Error('Upstream did not close')), 1500).unref(),
    ),
  ]);
});

test('WebSocket validates JSON and correlates results without dropping its active request', async () => {
  const ws = new WebSocket(base.replace('http:', 'ws:') + '/ws/compare', { origin: base });
  await once(ws, 'open');
  const received = [];
  ws.on('message', (bytes) => received.push(JSON.parse(bytes)));
  ws.send('null');
  await once(ws, 'message');
  assert.match(received.at(-1).error, /failed/);
  ws.send(JSON.stringify({ id: 'a', text: 'first', task: 'sentiment' }));
  ws.send(JSON.stringify({ id: 'b', text: 'second', task: 'sentiment' }));
  ws.send(JSON.stringify({ id: 'c', text: 'third', task: 'sentiment' }));
  while (!received.some((item) => item.id === 'a')) await once(ws, 'message');
  assert.equal(received.find((item) => item.id === 'a').direct.result, 'first');
  assert.match(received.find((item) => item.id === 'b').error, /already running/);
  assert.match(received.find((item) => item.id === 'c').error, /already running/);
  ws.close();
  await once(ws, 'close');
});
