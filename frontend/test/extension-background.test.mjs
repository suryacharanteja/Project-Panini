import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import {
  TabWorkspace,
  website,
} from '../../panini-core/capabilities/browser/extension/workspace.mjs';
import { createHmac, randomBytes } from 'node:crypto';

const pairing = { device_id: 'a'.repeat(32), secret: 'b'.repeat(64) };
const proof = (role, first, second) =>
  createHmac('sha256', Buffer.from(pairing.secret, 'hex'))
    .update(`panini-extension-v1|${role}|${first}|${second}`)
    .digest('hex');

/** Act as the paired PANINI: answer the extension's hello and complete the handshake. */
async function authenticate(socket, sent) {
  await socket.onopen();
  const hello = sent.at(-1);
  const serverNonce = randomBytes(32).toString('base64url');
  await socket.onmessage({
    data: JSON.stringify({
      type: 'challenge',
      nonce: serverNonce,
      proof: proof('server', hello.nonce, serverNonce),
    }),
  });
  assert.deepEqual(sent.at(-1), { type: 'auth', proof: proof('client', serverNonce, hello.nonce) });
  await socket.onmessage({
    data: JSON.stringify({ type: 'connected', capabilities: ['workspace-v1'] }),
  });
  return hello;
}

test('popup waits for worker restoration and reconnects the retained group after switching to a single tab', async () => {
  let handler, releaseStorage;
  const storageGate = new Promise((resolve) => {
    releaseStorage = resolve;
  });
  const tabs = [7, 8].map((id) => ({
    id,
    windowId: 1,
    groupId: 5,
    active: id === 7,
    title: `Page ${id}`,
    url: `https://example.com/${id}`,
  }));
  const session = {
    workspace: {
      id: 'old',
      revision: 'old-revision',
      group_id: 5,
      window_id: 1,
      selected_tab: 8,
      allow_new_tabs: true,
      tabs: tabs.map((tab) => ({ ...tab, grant: `old-${tab.id}` })),
    },
  };
  const sockets = [];
  const sent = [];
  class Socket {
    static OPEN = 1;
    static CONNECTING = 0;
    constructor() {
      this.readyState = 1;
      sockets.push(this);
    }
    send(data) {
      sent.push(JSON.parse(data));
    }
  }
  const context = vm.createContext({
    URL,
    TabWorkspace,
    website,
    AbortController,
    crypto,
    btoa,
    TextEncoder,
    WebSocket: Socket,
    setTimeout: () => 1,
    clearTimeout() {},
    setInterval() {},
    chrome: {
      storage: {
        local: {
          get: async () => {
            await storageGate;
            return { pairing };
          },
        },
        session: { get: async () => session, set: async (data) => Object.assign(session, data) },
      },
      action: { setBadgeText() {}, setBadgeBackgroundColor() {} },
      permissions: { contains: async () => true },
      tabGroups: {
        query: async () => [{ id: 5, windowId: 1, title: 'JevRev' }],
        get: async () => ({ id: 5, windowId: 1, title: 'JevRev' }),
        onRemoved: { addListener() {} },
      },
      tabs: {
        get: async (id) => tabs.find((tab) => tab.id === id),
        query: async (query) => (query.active ? [tabs[0]] : tabs),
        sendMessage: async () => ({ marker: 'current', version: '2.0.2' }),
        onUpdated: { addListener() {} },
        onRemoved: { addListener() {} },
      },
      runtime: {
        getManifest: () => ({ version: '2.0.2' }),
        onMessage: {
          addListener(fn) {
            handler = fn;
          },
        },
      },
    },
  });
  vm.runInContext(
    readFileSync(
      new URL('../../panini-core/capabilities/browser/extension/background.js', import.meta.url),
      'utf8',
    ).replace(/^import .*;\r?\n/, ''),
    context,
  );
  const call = (msg) => new Promise((resolve) => handler(msg, {}, resolve));
  let answered = false;
  const pending = call({ kind: 'state' }).then((state) => {
    answered = true;
    return state;
  });
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(answered, false, 'Do not publish an empty state while storage is still loading');
  releaseStorage();
  const restored = await pending;
  assert.equal(restored.workspace.group_id, 5);
  assert.equal(restored.workspace.selected_tab, 8);
  assert.equal(restored.tab, 8);
  assert.notEqual(restored.workspace.id, 'old');
  assert.equal(restored.workspaces.length, 1);
  await new Promise((resolve) => setImmediate(resolve));
  await authenticate(sockets[0], sent);
  await call({ kind: 'attach', on: true, tabId: 7 });
  const single = await call({ kind: 'state' });
  assert.equal(single.workspace, null);
  assert.equal(single.workspaces[0].group_id, 5);
  const reconnected = await call({ kind: 'workspaceReconnect', groupId: 5, tabIds: [7, 8] });
  assert.equal(reconnected.workspace.group_id, 5);
  assert.equal(session.workspace.group_id, 5);
  assert.equal(sent.at(-1).workspace.id, reconnected.workspace.id);
  const same = await call({ kind: 'workspaceReconnect', groupId: 5, tabIds: [7, 8] });
  assert.equal(same.workspace.id, reconnected.workspace.id);
});

test('attachment is session scoped, announced immediately, and relay destinations stay local', async () => {
  let handler, removed;
  const sent = [];
  const sockets = [];
  const session = {};
  let contentReady = false;
  let holdNavigation = false;
  const injected = [];
  const updates = new Set();
  class Socket {
    static OPEN = 1;
    static CONNECTING = 0;
    constructor(url) {
      this.url = url;
      this.readyState = 1;
      sockets.push(this);
    }
    send(data) {
      sent.push(JSON.parse(data));
    }
    close() {
      this.onclose?.();
    }
  }
  const context = vm.createContext({
    URL,
    TabWorkspace,
    website,
    AbortController,
    crypto,
    btoa,
    TextEncoder,
    WebSocket: Socket,
    setTimeout: () => 1,
    clearTimeout() {},
    setInterval() {},
    chrome: {
      storage: {
        local: {
          get: async () => ({ url: 'ws://remote.example/ws/extension', tab: 999, pairing }),
          set: async () => {},
        },
        session: {
          get: async () => session,
          set: async (value) => Object.assign(session, value),
          remove: async () => {
            delete session.tab;
          },
        },
      },
      action: { setBadgeText() {}, setBadgeBackgroundColor() {} },
      tabs: {
        get: async (id) => ({
          id,
          active: true,
          status: 'complete',
          windowId: 1,
          title: 'Test tab',
          url: id === 8 ? 'chrome://extensions/' : 'https://example.com/',
        }),
        query: async (query) =>
          query.active
            ? [{ id: 8, url: 'chrome://extensions/' }]
            : [
                { id: 7, title: 'Test tab', url: 'https://example.com/' },
                { id: 8, url: 'chrome://extensions/' },
                { id: 10, url: 'https://chromewebstore.google.com/' },
              ],
        sendMessage: async (id) => {
          if (!contentReady || id === 9) throw new Error('No content script');
          return { marker: 'm1', version: '1.4.1' };
        },
        update: async (id) => {
          assert.equal(updates.size, 2, 'Navigation listener must exist before tabs.update');
          if (!holdNavigation) for (const listener of updates) listener(id, { status: 'complete' });
        },
        onUpdated: {
          addListener: (fn) => updates.add(fn),
          removeListener: (fn) => updates.delete(fn),
        },
        onRemoved: {
          addListener(fn) {
            removed = fn;
          },
        },
      },
      scripting: {
        executeScript: async ({ target }) => {
          if (target.tabId === 9) throw new Error('Website permission was denied');
          injected.push(target.tabId);
          contentReady = true;
        },
      },
      runtime: {
        getManifest: () => ({ version: '1.4.1' }),
        onMessage: {
          addListener(fn) {
            handler = fn;
          },
        },
      },
    },
  });
  vm.runInContext(
    readFileSync(
      new URL('../../panini-core/capabilities/browser/extension/background.js', import.meta.url),
      'utf8',
    ).replace(/^import .*;\r?\n/, ''),
    context,
  );
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(sockets[0].url, 'ws://127.0.0.1:3200/ws/extension');
  const hello = await authenticate(sockets[0], sent);
  assert.equal(hello.tab, null); // old persisted tab grant is not restored
  assert.equal(hello.protocol, 4);
  assert.equal(hello.device_id, pairing.device_id);
  assert.equal(JSON.stringify(sent).includes(pairing.secret), false, 'The secret never leaves');
  const call = (msg) => new Promise((resolve) => handler(msg, {}, resolve));
  const state = await call({ kind: 'state' });
  assert.equal(state.currentSupported, false);
  assert.deepEqual(
    Array.from(state.tabs, (tab) => tab.id),
    [7],
  );
  assert.match((await call({ kind: 'attach', on: true, tabId: 8 })).error, /Chrome settings/);
  assert.match((await call({ kind: 'attach', on: true })).error, /Choose/);
  assert.equal((await call({ kind: 'attach', on: true, tabId: 7 })).tab, 7);
  assert.deepEqual(injected, [7]);
  assert.match((await call({ kind: 'attach', on: true, tabId: 9 })).error, /permission/);
  assert.equal((await call({ kind: 'state' })).tab, 7);
  assert.equal(session.tab, 7);
  assert.equal(sent.at(-1).tab, 7);
  assert.equal(sent.at(-1).tab_title, 'Test tab');
  assert.equal(sent.at(-1).protocol, 4);
  assert.equal((await call({ kind: 'state' })).connected, true);
  const grant = session.attachmentId;
  await sockets[0].onmessage({
    data: JSON.stringify({
      id: 'stale',
      action: {
        kind: 'snapshot',
        expected_tab: 8,
        expected_attachment: grant,
      },
    }),
  });
  assert.equal(sent.at(-1).ok, false);
  assert.match(sent.at(-1).error, /changed/);
  await sockets[0].onmessage({
    data: JSON.stringify({
      id: 'valid',
      action: {
        kind: 'snapshot',
        expected_tab: 7,
        expected_attachment: grant,
      },
    }),
  });
  assert.equal(sent.at(-1).ok, true);
  await sockets[0].onmessage({
    data: JSON.stringify({
      id: 'navigate',
      action: {
        kind: 'navigate',
        url: 'https://example.com/next',
        expected_tab: 7,
        expected_attachment: grant,
      },
    }),
  });
  assert.equal(sent.at(-1).ok, true);
  assert.equal(updates.size, 1, 'Navigation listener must be cleaned up');
  holdNavigation = true;
  const pendingNavigation = sockets[0].onmessage({
    data: JSON.stringify({
      id: 'cancel-navigation',
      action: {
        kind: 'navigate',
        url: 'https://example.com/slow',
        expected_tab: 7,
        expected_attachment: grant,
      },
    }),
  });
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(updates.size, 2);
  await sockets[0].onmessage({ data: JSON.stringify({ type: 'cancel', id: 'cancel-navigation' }) });
  await pendingNavigation;
  assert.equal(sent.at(-1).ok, false);
  assert.match(sent.at(-1).error, /cancelled/);
  assert.equal(
    updates.size,
    1,
    'Stop removes the navigation waiter and releases the command queue',
  );
  holdNavigation = false;
  assert.match(
    (await call({ kind: 'setUrl', url: 'ws://remote.example/ws/extension' })).error,
    /local/,
  );
  await call({ kind: 'setUrl', url: 'ws://localhost:3100/ws/extension' });
  assert.equal(sockets[1].url, 'ws://localhost:3100/ws/extension');
  await authenticate(sockets[1], sent);
  sockets[0].onclose(); // an old connection cannot clear its replacement
  removed(7);
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(sent.at(-1).tab, null);
  sockets[1].onclose({ code: 1008, reason: 'Another Jev extension is connected' });
  assert.match((await call({ kind: 'state' })).lastError, /Another/);
});

test('the extension obeys only a server that proves the pairing, and only its own popup', async () => {
  let handler;
  const sent = [];
  const sockets = [];
  const local = { pairing };
  const requests = [];
  class Socket {
    static OPEN = 1;
    static CONNECTING = 0;
    constructor(url) {
      this.url = url;
      this.readyState = 1;
      sockets.push(this);
    }
    send(data) {
      sent.push(JSON.parse(data));
    }
    close() {
      this.closed = true;
      this.readyState = 3;
      this.onclose?.({ code: 1000 });
    }
  }
  const context = vm.createContext({
    URL,
    TabWorkspace,
    website,
    AbortController,
    crypto,
    btoa,
    TextEncoder,
    WebSocket: Socket,
    setTimeout: () => 1,
    clearTimeout() {},
    setInterval() {},
    fetch: async (url, init) => {
      requests.push({ url, body: JSON.parse(init.body) });
      return { ok: true, json: async () => pairing };
    },
    chrome: {
      storage: {
        local: {
          get: async () => local,
          set: async (value) => Object.assign(local, value),
          remove: async (key) => delete local[key],
        },
        session: { get: async () => ({}), set: async () => {} },
      },
      action: { setBadgeText() {}, setBadgeBackgroundColor() {} },
      tabs: {
        get: async (id) => ({ id, title: 'Tab', url: 'https://example.com/' }),
        query: async () => [],
        onUpdated: { addListener() {} },
        onRemoved: { addListener() {} },
      },
      runtime: {
        id: 'claw',
        getManifest: () => ({ version: '3.0.0' }),
        onMessage: {
          addListener(fn) {
            handler = fn;
          },
        },
      },
    },
  });
  vm.runInContext(
    readFileSync(
      new URL('../../panini-core/capabilities/browser/extension/background.js', import.meta.url),
      'utf8',
    ).replace(/^import .*;\r?\n/, ''),
    context,
  );
  await new Promise((resolve) => setImmediate(resolve));
  const call = (msg, sender = {}) => new Promise((resolve) => handler(msg, sender, resolve));
  await sockets[0].onopen();
  const hello = sent.at(-1);
  // Nothing is executed before the server has proven the pairing.
  await sockets[0].onmessage({ data: JSON.stringify({ id: 'early', action: { kind: 'ping' } }) });
  assert.equal(sent.at(-1), hello);
  // An impostor listening on the same port cannot produce the proof.
  await sockets[0].onmessage({
    data: JSON.stringify({ type: 'challenge', nonce: 'n'.repeat(43), proof: '0'.repeat(64) }),
  });
  assert.equal(sockets[0].closed, true);
  assert.equal(sent.at(-1), hello, 'No auth answer is sent to an unverified server');
  assert.match((await call({ kind: 'state' })).lastError, /could not prove/);
  // Content scripts run inside web pages and may not drive the extension.
  const fromPage = await call({ kind: 'attach', on: true, tabId: 7 }, { tab: { id: 7 } });
  assert.match(fromPage.error, /popup/);
  // Pairing is redeemed only with the local app, and the code is normalized.
  local.pairing = undefined;
  await call({ kind: 'forget' });
  const before = sockets.length;
  assert.equal((await call({ kind: 'state' })).paired, false);
  assert.match((await call({ kind: 'state' })).lastError, /Not paired/);
  assert.match((await call({ kind: 'pair', code: 'abc' })).error, /8-character/);
  assert.equal((await call({ kind: 'pair', code: 'abcd-efgh' })).paired, true);
  assert.equal(requests[0].url, 'http://127.0.0.1:3200/api/extension/pair');
  assert.equal(requests[0].body.code, 'ABCDEFGH');
  assert.equal(JSON.stringify(local.pairing), JSON.stringify(pairing));
  assert.equal(sockets.length, before + 1, 'A new pairing connects immediately');
});
