import test from 'node:test';
import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';
import { createServer } from 'node:http';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { WebSocket } from 'ws';
import { createGateway } from '../server/gateway.mjs';
import { createSigninGate } from '../server/signin-gate.mjs';
import { createConsoleAuth, mintTicket } from '../server/console-auth.mjs';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..', '..');

const SECRET = 's'.repeat(43);
const TOKEN = 't'.repeat(43);
const GOOD = 'panini_vision=good';

// A stand-in for PANINI-SIGNIN: a session cookie is unlocked only after a successful attempt.
function fakeSignin() {
  const received = [];
  const open = new Set(); // Sessions the fake service currently holds unlocked.
  const server = createServer((req, res) => {
    let body = '';
    req.on('data', (chunk) => (body += chunk));
    req.on('end', () => {
      received.push({ method: req.method, url: req.url, cookie: req.headers.cookie, body });
      const unlocked = [...open].some((cookie) => (req.headers.cookie || '').includes(cookie));
      if (req.url === '/api/auth/status')
        return res.end(JSON.stringify({ unlocked, enrolled: true }));
      if (req.url === '/api/auth/attempt') {
        const ok = JSON.parse(body || '{}').phrase === 'right';
        if (!ok) {
          res.statusCode = 422;
          return res.end(JSON.stringify({ error: 'The check did not pass.' }));
        }
        open.add(GOOD);
        res.setHeader('Set-Cookie', `${GOOD}; HttpOnly; SameSite=Strict; Path=/; Max-Age=300`);
        return res.end(JSON.stringify({ unlocked: true }));
      }
      if (req.url === '/api/auth/lock') {
        open.clear();
        res.setHeader('Set-Cookie', 'panini_vision=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0');
        return res.end(JSON.stringify({ unlocked: false }));
      }
      if (req.url === '/' || req.url.startsWith('/?')) {
        res.setHeader('content-type', 'text/html');
        return res.end('<title>PANINI-SIGNIN</title>');
      }
      if (req.url === '/auth.js') {
        res.setHeader('content-type', 'text/javascript');
        return res.end('// assets');
      }
      res.statusCode = 404;
      res.end('{}');
    });
  });
  return new Promise((resolve) =>
    server.listen(0, '127.0.0.1', () =>
      resolve({
        url: `http://127.0.0.1:${server.address().port}`,
        received,
        close: () => new Promise((done) => server.close(done)),
      }),
    ),
  );
}

async function runtime({ signinUrl } = {}) {
  const received = [];
  const backend = createServer((req, res) => {
    received.push({ url: req.url, headers: req.headers });
    res.setHeader('content-type', 'application/json');
    res.end('{"status":"ok"}');
  });
  await new Promise((resolve) => backend.listen(0, '127.0.0.1', resolve));
  const signin = signinUrl === undefined ? await fakeSignin() : null;
  const gate = createSigninGate({ url: signinUrl ?? signin.url });
  const gateway = createGateway({
    backendUrl: `http://127.0.0.1:${backend.address().port}`,
    searchUrl: '',
    signin: gate,
    consoleToken: TOKEN,
  });
  await new Promise((resolve) => gateway.server.listen(0, '127.0.0.1', resolve));
  const base = `http://127.0.0.1:${gateway.server.address().port}`;
  return {
    base,
    received,
    signin,
    close: async () => {
      await gateway.close();
      await new Promise((resolve) => backend.close(resolve));
      await signin?.close();
    },
  };
}

const post = (url, headers = {}, body = '{}') =>
  fetch(url, {
    method: 'POST',
    headers: { 'content-type': 'application/json', ...headers },
    body,
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

    // Console routes need a face-and-voice session, even while PANINI is running.
    for (const path of ['/api/browser/run-stream', '/api/compare', '/search/api/ask']) {
      const refused = await post(base + path);
      assert.equal(refused.status, 401, path);
      assert.equal((await refused.json()).code, 'CONSOLE_SIGN_IN_REQUIRED');
    }
    const page = await fetch(base + '/', { headers: { accept: 'text/html' }, redirect: 'manual' });
    assert.equal(page.status, 302);
    assert.equal(page.headers.get('location'), '/signin?next=%2F');
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

    // Signed in with face and voice: the console reaches the backend with the service token.
    // Face and voice pass first; the fake sign-in service then holds the session open.
    await post(base + '/api/auth/attempt', {}, JSON.stringify({ phrase: 'right' }));
    const session = { cookie: GOOD };
    assert.equal((await post(base + '/api/compare', session)).status, 200);
    assert.equal(received.at(-1).headers['x-panini-console'], TOKEN);
    assert.equal(received.at(-1).headers.cookie, undefined, 'The session cookie stays at the gateway');
    assert.equal((await fetch(base + '/activity', { headers: session })).status, 200);
    const titleOf = async (path) =>
      ((await (await fetch(base + path, { headers: session })).text()).match(/<title>([^<]*)/) ||
        [])[1];
    const adminBuilt = existsSync(join(ROOT, 'frontend/public/admin/index.html'));
    if (adminBuilt) assert.equal(await titleOf('/'), 'PANINI Admin');
    for (const path of ['/browser', '/activity', '/agent'])
      assert.equal((await fetch(base + path, { headers: session })).status, 200, path);
    assert.equal((await fetch(base + '/build/../../etc', { headers: session })).status, 404);
    await fetch(base + '/api/admin/v1/settings', { headers: session });
    assert.equal(received.at(-1).headers['x-panini-console'], TOKEN);

    // Locking ends the console on the sign-in service too.
    const lockedOut = await post(base + '/auth/logout', session);
    assert.equal(lockedOut.status, 200);
    assert.match(lockedOut.headers.get('set-cookie'), /panini_vision=; .*Max-Age=0/);
    assert.equal((await post(base + '/api/compare', session)).status, 401);
  } finally {
    await close();
  }
});

test('sign-in pages and APIs are relayed to PANINI-SIGNIN, and only a passing check unlocks the console', async () => {
  const { base, signin, close } = await runtime();
  try {
    // The sign-in page and its assets reach the sign-in service unchanged.
    const page = await fetch(base + '/signin?next=%2Factivity');
    assert.equal(page.status, 200);
    assert.match(await page.text(), /PANINI-SIGNIN/);
    assert.equal(signin.received.at(-1).url, '/');
    assert.equal((await fetch(base + '/auth.js')).status, 200);

    // A failed check stays locked and returns the service's message.
    const failed = await post(base + '/api/auth/attempt', {}, JSON.stringify({ phrase: 'wrong' }));
    assert.equal(failed.status, 422);
    assert.equal(failed.headers.get('set-cookie'), null);
    assert.equal((await post(base + '/api/compare')).status, 401);

    // A passing check sets the sign-in cookie through the gateway, which unlocks the console.
    const passed = await post(base + '/api/auth/attempt', {}, JSON.stringify({ phrase: 'right' }));
    assert.equal(passed.status, 200);
    const cookie = passed.headers.get('set-cookie');
    assert.match(cookie, /^panini_vision=good; HttpOnly; SameSite=Strict/);
    assert.equal((await post(base + '/api/compare', { cookie: cookie.split(';')[0] })).status, 200);
  } finally {
    await close();
  }
});

test('shell tickets never open the console; they lead to the face-and-voice page', async () => {
  const { base, close } = await runtime();
  try {
    const ticket = mintTicket(SECRET);
    const login = await fetch(`${base}/auth/login?ticket=${ticket}&next=/activity`, {
      redirect: 'manual',
    });
    assert.equal(login.status, 303);
    assert.equal(login.headers.get('location'), '/signin?next=%2Factivity');
    assert.equal(login.headers.get('set-cookie'), null, 'A ticket sets no session');
    assert.equal((await post(base + '/api/compare')).status, 401, 'A ticket is not a session');
  } finally {
    await close();
  }
});

test('the console stays locked when PANINI-SIGNIN is missing or unreachable', async () => {
  const gate = createSigninGate({ url: '' });
  assert.equal(await gate.unlocked('panini_vision=good'), false, 'no service configured');
  const unreachable = createSigninGate({ url: 'http://127.0.0.1:9', timeoutMs: 500 });
  assert.equal(await unreachable.unlocked('panini_vision=good'), false, 'service down');

  const { base, close } = await runtime({ signinUrl: 'http://127.0.0.1:9' });
  try {
    assert.equal((await post(base + '/api/compare', { cookie: GOOD })).status, 401);
    const signin = await fetch(base + '/signin');
    assert.equal(signin.status, 502);
    assert.equal((await signin.json()).code, 'SIGNIN_UNAVAILABLE');
  } finally {
    await close();
  }
});

test('the comparison socket requires a face-and-voice session', async () => {
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
    await post(base + '/api/auth/attempt', {}, JSON.stringify({ phrase: 'right' }));
    const opened = await new Promise((resolve) => {
      const socket = new WebSocket(url, { headers: { cookie: GOOD } });
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

test('shell tickets and console sessions still expire on their own terms', () => {
  let now = 1_000_000;
  const auth = createConsoleAuth({ secret: SECRET, now: () => now, sessionTtl: 60_000 });
  const expired = auth.ticket(1000);
  now += 1001;
  assert.equal(auth.redeem(expired), null);
  assert.equal(auth.redeem(mintTicket('x'.repeat(43), { now })), null, 'other secret');
  assert.throws(() => createConsoleAuth({ secret: 'short' }), /at least 32/);
});
