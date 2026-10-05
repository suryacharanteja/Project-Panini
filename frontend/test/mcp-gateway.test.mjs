import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { once } from 'node:events';
import { createGateway } from '../server/gateway.mjs';

test('MCP forwards only its credentials/session headers, streams, and refuses browser Origin', async () => {
  const seen = [];
  const backend = createServer(async (req, res) => {
    let body = '';
    for await (const chunk of req) body += chunk;
    seen.push({ headers: req.headers, body });
    res.writeHead(200, { 'content-type': 'text/event-stream', 'mcp-session-id': 'bound-session' });
    res.write('event: message\ndata: {"part":1}\n\n');
    res.end('event: message\ndata: {"part":2}\n\n');
  }).listen(0, '127.0.0.1');
  await once(backend, 'listening');
  const gateway = createGateway({
    backendUrl: `http://127.0.0.1:${backend.address().port}`,
    consoleToken: 'must-not-be-forwarded',
    consoleAuth: { authenticated: () => false },
    allowedOrigins: ['https://trusted.example'],
  });
  gateway.server.listen(0, '127.0.0.1');
  await once(gateway.server, 'listening');
  const base = `http://127.0.0.1:${gateway.server.address().port}`;
  try {
    const response = await fetch(base + '/mcp', {
      method: 'POST',
      headers: {
        authorization: 'Bearer client-token',
        'mcp-session-id': 'session',
        'mcp-protocol-version': '2025-11-25',
        'last-event-id': 'last',
        'content-type': 'application/json',
        accept: 'application/json, text/event-stream',
        'x-panini-console': 'forged',
        cookie: 'owner=private',
      },
      body: JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'tools/list' }),
    });
    assert.equal(response.status, 200);
    assert.equal(response.headers.get('mcp-session-id'), 'bound-session');
    assert.match(await response.text(), /"part":2/);
    assert.equal(seen[0].headers.authorization, 'Bearer client-token');
    assert.equal(seen[0].headers['mcp-session-id'], 'session');
    assert.equal(seen[0].headers['mcp-protocol-version'], '2025-11-25');
    assert.equal(seen[0].headers['last-event-id'], 'last');
    for (const header of ['origin', 'x-panini-console', 'cookie'])
      assert.equal(seen[0].headers[header], undefined);
    for (const origin of [base, 'https://trusted.example', '']) {
      const denied = await fetch(base + '/mcp', { method: 'POST', headers: { origin } });
      assert.equal(denied.status, 403);
    }
    assert.equal(seen.length, 1);
  } finally {
    await gateway.close();
    backend.closeAllConnections();
    await new Promise((resolve) => backend.close(resolve));
  }
});
