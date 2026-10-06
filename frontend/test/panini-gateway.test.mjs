import test from 'node:test';
import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';
import { createServer } from 'node:http';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { WebSocket } from 'ws';
import { createGateway } from '../server/gateway.mjs';
import { createConsoleAuth, mintTicket } from '../server/console-auth.mjs';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..', '..');

const SECRET = 's'.repeat(43);
const TOKEN = 't'.repeat(43);

async function runtime() {
  const received = [];
  const backend = createServer((req, res) => {
    received.push({ url: req.url, headers: req.headers });
    res.setHeader('content-type', 'application/json');
    res.end('{"status":"ok"}');
  });
  await new Promise((resolve) => backend.listen(0, '127.0.0.1', resolve));
  const gateway = createGateway({
    backendUrl: `http://127.0.0.1:${backend.address().port}`,
    searchUrl: '',
    consoleAuth: createConsoleAuth({ secret: SECRET }),
    consoleToken: TOKEN,
  });
  await new Promise((resolve) => gateway.server.listen(0, '127.0.0.1', resolve));
  const base = `http://127.0.0.1:${gateway.server.address().port}`;
  return {
    base,
    received,
    close: async () => {
      await gateway.close();
      await new Promise((resolve) => backend.close(resolve));
    },
  };
}

const post = (url, headers = {}) =>
  fetch(url, {
    method: 'POST',
    headers: { 'content-type': 'application/json', ...headers },
    body: '{}',
    redirect: 'manual',
  });

test('one gateway serves the PANINI shell API, the extension pairing and the signed-in console', async () => {
  const { base, received, close } = await runtime();
  try {
    // The shell API keeps its own scoped credentials and nothing else.
    const headers = {
      authorization: 'Bearer test',
      'x-panini-instance': 'instance',
      'x-panini-lease-epoch': '2',
      cookie: 'private=discard; panini_observer_abc=viewer',
    };
    assert.equal((await fetch(base + '/api/panini/sessions/test', { headers })).status, 200);
    const shell = received.at(-1).headers;
    assert.equal(shell.authorization, 'Bearer test');
    assert.equal(shell['x-panini-lease-epoch'], '2');
    assert.equal(shell.cookie, 'panini_observer_abc=viewer');
    assert.equal(shell['x-panini-console'], undefined, 'Shell calls never carry console authority');
    assert.equal(
      (await fetch(base + '/api/panini/health', { headers: { origin: 'https://evil.example' } }))
        .status,
      403,
    );

    // Console routes need a signed-in session, even while PANINI is running.
    for (const path of ['/api/browser/run-stream', '/api/compare', '/search/api/ask'])
      assert.equal((await post(base + path)).status, 401);
    const page = await fetch(base + '/', { headers: { accept: 'text/html' }, redirect: 'manual' });
    assert.equal(page.status, 302);
    assert.equal(page.headers.get('location'), '/auth-required.html');
    for (const open of ['/auth-required.html', '/browser-demo.html', '/brand.css'])
      assert.equal((await fetch(base + open)).status, 200, open);

    // The extension redeems a pairing code with its own origin, not console authority.
    const extension = 'chrome-extension://claw';
    assert.equal((await post(base + '/api/extension/pair', { origin: extension })).status, 200);
    assert.equal(received.at(-1).headers.origin, extension);
    assert.equal(received.at(-1).headers['x-panini-console'], undefined);
    assert.equal(
      (await post(base + '/api/extension/pair', { origin: 'https://evil.example' })).status,
      403,
    );
    assert.equal((await post(base + '/api/compare', { origin: extension })).status, 403);

    // One-time sign-in: invalid and reused tickets fail; next= stays on this site.
    const bad = await fetch(base + '/auth/login?ticket=v1.1.aaaaaaaaaaaaaaaa.bad', {
      redirect: 'manual',
    });
    assert.equal(bad.headers.get('location'), '/auth-required.html?expired=1');
    const ticket = mintTicket(SECRET);
    const login = await fetch(`${base}/auth/login?ticket=${ticket}&next=//evil.example`, {
      redirect: 'manual',
    });
    assert.equal(login.status, 303);
    assert.equal(login.headers.get('location'), '/');
    const cookie = login.headers.get('set-cookie');
    assert.match(cookie, /HttpOnly; SameSite=Strict; Path=\//);
    const reused = await fetch(`${base}/auth/login?ticket=${ticket}`, { redirect: 'manual' });
    assert.equal(reused.headers.get('location'), '/auth-required.html?expired=1');

    // Signed in: the console reaches the backend with the per-launch service token.
    const session = { cookie: cookie.split(';')[0] };
    assert.equal((await post(base + '/api/compare', session)).status, 200);
    assert.equal(received.at(-1).headers['x-panini-console'], TOKEN);
    assert.equal(
      received.at(-1).headers.cookie,
      undefined,
      'The session cookie stays at the gateway',
    );
    assert.equal((await fetch(base + '/activity', { headers: session })).status, 200);
    // PANINI Admin is home (when built); Compare moved to the Playground; old pages still work.
    const titleOf = async (path) =>
      ((await (await fetch(base + path, { headers: session })).text()).match(/<title>([^<]*)/) ||
        [])[1];
    const adminBuilt = existsSync(join(ROOT, 'frontend/public/admin/index.html'));
    assert.equal(
      await titleOf('/'),
      adminBuilt ? 'PANINI Admin' : 'JevRev — Structured intelligence',
    );
    if (adminBuilt)
      for (const path of [
        '/inbox',
        '/build/connectors',
        '/capabilities/code',
        '/settings/keys',
        '/spaces',
        '/spaces/space_1/pages/page_2',
      ])
        assert.equal(await titleOf(path), 'PANINI Admin', path);
    assert.match(await titleOf('/playground/compare'), /JevRev/);
    for (const path of ['/browser', '/activity', '/agent'])
      assert.equal((await fetch(base + path, { headers: session })).status, 200, path);
    assert.equal((await fetch(base + '/build/../../etc', { headers: session })).status, 404);
    // The Admin API is a console route: the gateway forwards it with console authority.
    await fetch(base + '/api/admin/v1/settings', { headers: session });
    assert.equal(received.at(-1).headers['x-panini-console'], TOKEN);
    assert.equal((await post(base + '/auth/logout', session)).status, 200);
    assert.equal((await post(base + '/api/compare', session)).status, 401);
  } finally {
    await close();
  }
});

test('the comparison socket requires a signed-in console', async () => {
  const { base, close } = await runtime();
  const url = base.replace('http', 'ws') + '/ws/compare';
  try {
    const refused = await new Promise((resolve) => {
      const socket = new WebSocket(url);
      socket.on('unexpected-response', (_req, res) => resolve(res.statusCode));
      socket.on('open', () => resolve('open'));
      socket.on('error', () => {});
    });
    assert.equal(refused, 403);
    const login = await fetch(`${base}/auth/login?ticket=${mintTicket(SECRET)}`, {
      redirect: 'manual',
    });
    const cookie = login.headers.get('set-cookie').split(';')[0];
    const opened = await new Promise((resolve) => {
      const socket = new WebSocket(url, { headers: { cookie } });
      socket.on('open', () => {
        socket.close();
        resolve('open');
      });
      socket.on('unexpected-response', (_req, res) => resolve(res.statusCode));
    });
    assert.equal(opened, 'open');
  } finally {
    await close();
  }
});

test('console tickets and sessions expire', () => {
  let now = 1_000_000;
  const auth = createConsoleAuth({ secret: SECRET, now: () => now, sessionTtl: 60_000 });
  const expired = auth.ticket(1000);
  now += 1001;
  assert.equal(auth.redeem(expired), null);
  assert.equal(auth.redeem(mintTicket('x'.repeat(43), { now })), null, 'other secret');
  const session = auth.redeem(auth.ticket());
  const request = { headers: { cookie: `other=1; panini_console=${session}` } };
  assert.equal(auth.authenticated(request), true);
  now += 59_000;
  assert.equal(auth.authenticated(request), true, 'use slides the expiry');
  now += 60_001;
  assert.equal(auth.authenticated(request), false);
  assert.throws(() => createConsoleAuth({ secret: 'short' }), /at least 32/);
});
