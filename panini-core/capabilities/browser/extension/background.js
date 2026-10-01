import { TabWorkspace, website } from './workspace.mjs';
/* Local relay for commands against the tab explicitly attached in the popup.
 *
 * Connections are mutually authenticated with the pairing secret: the app must
 * prove it is the paired PANINI before this extension accepts any action, so
 * another program listening on the same local port cannot drive your tabs.
 */
const DEFAULT_URL = 'ws://127.0.0.1:3200/ws/extension';
const VERSION = chrome.runtime.getManifest().version;
const PROTOCOL = 4;
const MAX_RETRY = 30000;

let ws = null;
let retry = null;
let retryDelay = 1000;
let attachedTab = null;
let attachmentId = '';
let rejected = false;
let pairing = null; // { device_id, secret } from the one-time pairing code.

const state = {
  connected: false,
  url: DEFAULT_URL,
  tab: null,
  lastError: '',
  workspaceSupported: false,
};

const hex = (bytes) =>
  [...new Uint8Array(bytes)].map((b) => b.toString(16).padStart(2, '0')).join('');
function nonce() {
  const bytes = crypto.getRandomValues(new Uint8Array(32));
  return btoa(String.fromCharCode(...bytes)).replace(
    /[+/=]/g,
    (c) => ({ '+': '-', '/': '_', '=': '' })[c],
  );
}
async function proof(role, first, second) {
  const secret = new Uint8Array(pairing.secret.match(/../g).map((pair) => parseInt(pair, 16)));
  const key = await crypto.subtle.importKey(
    'raw',
    secret,
    { name: 'HMAC', hash: 'SHA-256' },
    false,
    ['sign'],
  );
  const message = new TextEncoder().encode(`panini-extension-v1|${role}|${first}|${second}`);
  return hex(await crypto.subtle.sign('HMAC', key, message));
}
function sameProof(a, b) {
  if (typeof a !== 'string' || a.length !== b.length) return false;
  let difference = 0;
  for (let i = 0; i < a.length; i++) difference |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return difference === 0;
}
const operations = new Map();
let queue = Promise.resolve();
let workspaceScope = '';
const workspace = new TabWorkspace(chrome, async (value) => {
  const nextScope = value ? `${value.id}:${value.revision}` : '';
  if (nextScope !== workspaceScope) abortOperations();
  workspaceScope = nextScope;
  attachedTab = workspace.current()?.id ?? null;
  attachmentId = workspace.current()?.grant || '';
  state.tab = attachedTab;
  await chrome.storage.session.set({ workspace: value, tab: attachedTab, attachmentId });
  await announce();
  badge(state.connected ? (value ? String(value.tabs.length) : '·') : 'OFF', '#986519');
});
function abortOperations() {
  for (const operation of operations.values()) operation.abort();
}
function scope() {
  return workspace.value
    ? { expected_workspace: workspace.value.id, expected_revision: workspace.value.revision }
    : {};
}

async function ensureContent(tabId) {
  const tab = await chrome.tabs.get(tabId);
  if (!website(tab.url))
    throw new Error(
      'Choose a website tab. Chrome settings and the Web Store cannot be controlled.',
    );
  try {
    const result = await chrome.tabs.sendMessage(tabId, { kind: 'marker' });
    if (result?.marker && result.version === VERSION) return;
  } catch {}
  // Tabs open before installation/reload do not have our current content script.
  await chrome.scripting.executeScript({ target: { tabId }, files: ['content.js'] });
  const result = await chrome.tabs.sendMessage(tabId, { kind: 'marker' });
  if (!result?.marker) throw new Error('The website did not respond. Refresh it and attach again.');
}

function localUrl(value) {
  const url = new URL(value || DEFAULT_URL);
  if (
    url.protocol !== 'ws:' ||
    !['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname) ||
    url.pathname !== '/ws/extension' ||
    url.username ||
    url.password ||
    url.search ||
    url.hash
  )
    throw new Error('Use a local ws://localhost:PORT/ws/extension address.');
  return url.href;
}

async function load() {
  const saved = await chrome.storage.local.get(['url', 'pairing']);
  try {
    state.url = localUrl(saved.url);
  } catch {
    state.url = DEFAULT_URL;
  }
  pairing =
    /^[a-f0-9]{32}$/.test(saved.pairing?.device_id) && /^[a-f0-9]{64}$/.test(saved.pairing?.secret)
      ? saved.pairing
      : null;
  const session = await chrome.storage.session.get(['tab', 'attachmentId', 'workspace']);
  if (session.workspace) {
    await workspace.restore(session.workspace);
    // A revoked workspace never falls back to a persisted single-tab grant.
    if (!workspace.value) await workspace.detach();
    return;
  }
  attachedTab = session.tab ?? null;
  if (attachedTab !== null && !(await chrome.tabs.get(attachedTab).catch(() => null)))
    attachedTab = null;
  state.tab = attachedTab;
  attachmentId = attachedTab == null ? '' : session.attachmentId || crypto.randomUUID();
  await chrome.storage.session.set({ tab: attachedTab, attachmentId });
}

function badge(text, color) {
  chrome.action.setBadgeText({ text });
  chrome.action.setBadgeBackgroundColor({ color });
}

function send(msg) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(msg));
    return true;
  }
  return false;
}

/** The current tab scope, as the app sees it. */
async function tabScope() {
  const id = attachedTab;
  const grant = attachmentId;
  const tab = id == null ? null : await chrome.tabs.get(id).catch(() => null);
  return {
    current: id === attachedTab && grant === attachmentId,
    hello: {
      type: 'hello',
      source: 'jev-rev-browxtension',
      version: VERSION,
      protocol: PROTOCOL,
      tab: tab ? id : null,
      attachment_id: grant,
      tab_title: tab?.title || '',
      tab_url: tab?.url || '',
      workspace: workspace.publicState(),
    },
  };
}

/** Report a changed tab scope; only ever on an authenticated connection. */
async function announce() {
  const socket = ws;
  const { current, hello } = await tabScope();
  if (current && socket === ws && state.connected) send(hello);
}

function scheduleRetry() {
  clearTimeout(retry);
  retry = setTimeout(connect, retryDelay);
  retryDelay = Math.min(retryDelay * 2, MAX_RETRY);
}

function connect() {
  clearTimeout(retry);
  if (rejected) return;
  if (!pairing) {
    state.lastError = 'Not paired. Enter the pairing code shown by PANINI.';
    return;
  }
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return;

  const socket = new WebSocket(state.url);
  const current = { nonce: nonce(), verified: false };
  ws = socket;

  socket.onopen = async () => {
    if (ws !== socket) return;
    const { hello } = await tabScope();
    // Only an identifier and a fresh nonce are sent; the secret never leaves the extension.
    if (ws === socket) send({ ...hello, device_id: pairing.device_id, nonce: current.nonce });
  };

  socket.onmessage = async (event) => {
    if (ws !== socket) return;
    let msg;
    try {
      msg = JSON.parse(event.data);
    } catch {
      return;
    }
    if (msg.type === 'challenge') {
      if (current.verified || typeof msg.nonce !== 'string') return socket.close();
      const expected = await proof('server', current.nonce, msg.nonce);
      if (ws !== socket) return;
      if (!sameProof(msg.proof, expected)) {
        state.lastError =
          'The app at this address could not prove it is your paired PANINI. Nothing was sent to it.';
        socket.close();
        return;
      }
      current.verified = true;
      const answer = await proof('client', msg.nonce, current.nonce);
      if (ws === socket) send({ type: 'auth', proof: answer });
      return;
    }
    // Nothing is accepted from a server that has not proven the pairing.
    if (!current.verified) return;
    if (msg.type === 'connected') {
      state.connected = true;
      state.workspaceSupported = msg.capabilities?.includes('workspace-v1') === true;
      state.lastError = '';
      retryDelay = 1000;
      badge(attachedTab != null ? 'ON' : '·', attachedTab != null ? '#1f8a4c' : '#3b5bdb');
      return;
    }
    if (!state.connected) return;
    if (msg.type === 'cancel') {
      operations.get(msg.id)?.abort();
      return;
    }
    const { id, action } = msg;
    if (!id || !action || operations.has(id)) return;
    const controller = new AbortController();
    operations.set(id, controller);
    const check = () => {
      if (controller.signal.aborted || ws !== socket || !state.connected)
        throw new Error('Browser action cancelled.');
    };
    const execute = async () => {
      try {
        check();
        const result = await run(action, check, controller.signal);
        check();
        send({ type: 'result', id, ok: true, result });
      } catch (e) {
        if (ws === socket) send({ type: 'result', id, ok: false, error: String(e.message || e) });
      } finally {
        operations.delete(id);
      }
    };
    queue = queue.then(execute, execute);
    await queue;
  };

  socket.onclose = (event) => {
    if (ws !== socket) return;
    state.connected = false;
    state.workspaceSupported = false;
    abortOperations();
    ws = null;
    badge('OFF', '#c2372f');
    if (event?.code === 1008) {
      rejected = true;
      state.lastError =
        event.reason ||
        'Connection declined. Close the other extension connection, then reconnect.';
      return;
    }
    // A stale saved address (for example an old port) must not strand the extension:
    // when it fails, the next attempt uses PANINI's default local address.
    if (state.url !== DEFAULT_URL) state.url = DEFAULT_URL;
    scheduleRetry(); // Backs off to 30 seconds while PANINI is closed.
  };

  socket.onerror = () => {
    if (ws !== socket) return;
    state.lastError = 'could not reach ' + state.url;
  };
}

/** The tab Jev-Rev is allowed to touch. Nothing runs without one. */
async function target(action) {
  if (workspace.value) return workspace.validate(action);
  if (action.expected_workspace) throw new Error('Workspace was detached.');
  if (attachedTab == null)
    throw new Error('No tab is attached. Click the Jev-Rev icon on the tab you want it to use.');
  const id = attachedTab;
  const grant = attachmentId;
  if (action.expected_tab !== id || action.expected_attachment !== grant)
    throw new Error('Attached tab changed. Review the target in Jev-Rev and run again.');
  try {
    await chrome.tabs.get(id);
  } catch {
    throw new Error('The attached tab was closed. Attach another one.');
  }
  if (id !== attachedTab || grant !== attachmentId)
    throw new Error('Attached tab changed. Review the target in Jev-Rev and run again.');
  return id;
}

const inPage = async (tabId, func, args = []) =>
  (await chrome.scripting.executeScript({ target: { tabId }, func, args }))[0]?.result;

async function run(action, check = () => {}, signal) {
  if (action?.kind === 'ping') return { pong: true, attached: attachedTab };
  const tabId = await target(action || {});
  check();
  switch (action.kind) {
    case 'select_tab':
      return await workspace.select(action.tab_id, action, check);
    case 'open_tab':
      return await workspace.open(action.url, action, check);
    case 'navigate': {
      const url = new URL(action.url);
      if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password)
        throw new Error('Only HTTP(S) navigation is supported.');
      if (!website(action.url)) throw new Error('Chrome Web Store pages cannot be controlled.');
      let complete;
      const loaded = new Promise((resolve, reject) => {
        complete = { resolve, reject };
      });
      const listener = (id, info) => {
        if (id === tabId && info.status === 'complete') complete.resolve();
      };
      // Register before requesting navigation: tabs.update can return the OLD
      // document's complete status before Chrome starts loading the new page.
      chrome.tabs.onUpdated.addListener(listener);
      const timer = setTimeout(
        () =>
          complete.reject(new Error('Navigation timed out. Wait for the page to load and retry.')),
        15000,
      );
      const cancelled = () => complete.reject(new Error('Browser action cancelled.'));
      signal?.addEventListener('abort', cancelled, { once: true });
      if (signal?.aborted) cancelled();
      try {
        check();
        await chrome.tabs.update(tabId, { url: action.url });
        await loaded;
      } finally {
        signal?.removeEventListener('abort', cancelled);
        clearTimeout(timer);
        chrome.tabs.onUpdated.removeListener(listener);
      }
      await target(action);
      check();
      await ensureContent(tabId);
      return { url: action.url };
    }

    case 'snapshot': {
      // the action space: what can be done here, indexed
      await ensureContent(tabId);
      await target(action);
      check();
      const snapshot = await chrome.tabs.sendMessage(tabId, {
        kind: 'snapshot',
        limit: action.limit ?? 120,
      });
      await target(action);
      check();
      return { ...snapshot, tab_id: tabId, workspace: workspace.publicState() };
    }

    case 'act': // execute one chosen action, refused if the page moved on
      if (action.id === 'wait') {
        await new Promise((resolve) => setTimeout(resolve, 500));
        await target(action);
        return { ok: 'waited' };
      }
      // Injected on demand; a page that navigated rejects the stale marker instead.
      await ensureContent(tabId);
      check();
      return await chrome.tabs.sendMessage(tabId, {
        kind: 'act',
        id: action.id,
        op: action.op,
        text: action.text,
        value: action.value,
        marker: action.marker,
      });

    case 'text':
      return {
        ...(await inPage(tabId, () => ({
          title: document.title,
          url: location.href,
          text: (document.body?.innerText || '').slice(0, 12000),
        }))),
        tab_id: tabId,
        workspace: workspace.publicState(),
      };

    case 'screenshot': {
      const tab = await chrome.tabs.get(tabId);
      if (!tab.active) throw new Error('Select the attached tab before capturing it.');
      return { dataUrl: await chrome.tabs.captureVisibleTab(tab.windowId, { format: 'png' }) };
    }

    default:
      throw new Error('Unknown action: ' + action.kind);
  }
}

/** Restart the connection to the current address with the current pairing. */
function reconnect() {
  abortOperations();
  const previous = ws;
  ws = null;
  state.connected = false;
  rejected = false;
  retryDelay = 1000;
  try {
    previous?.close();
  } catch {}
  connect();
}

chrome.runtime.onMessage.addListener((msg, sender, reply) => {
  (async () => {
    // Every command comes from our popup. Content scripts run inside web pages, so a
    // compromised page renderer must not be able to attach tabs or re-pair the extension.
    if (sender.tab || (sender.id && sender.id !== chrome.runtime.id))
      throw new Error('Use the JevRev Claw popup.');
    await ready;
    if (msg.kind === 'state') {
      await workspace.reconcile();
      const tab = attachedTab == null ? null : await chrome.tabs.get(attachedTab).catch(() => null);
      const tabs = await chrome.tabs.query({});
      const [active] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
      reply({
        ...state,
        paired: Boolean(pairing),
        workspace: workspace.publicState(),
        workspaces: await workspace.existing(),
        tab: attachedTab,
        tabTitle: tab?.title || null,
        tabUrl: tab?.url || '',
        currentTab: active?.id ?? null,
        currentSupported: website(active?.url),
        tabs: tabs
          .filter((t) => website(t.url))
          .map((t) => ({
            id: t.id,
            title: t.title || t.url,
            url: t.url,
            windowId: t.windowId,
            groupId: t.groupId,
          })),
      });
    } else if (msg.kind === 'workspaceCreate') {
      if (!state.connected || !state.workspaceSupported)
        throw new Error('Update and connect the JevRev app before creating a workspace.');
      abortOperations();
      await workspace.create(msg.tabIds, msg.allowNewTabs);
      reply({ workspace: workspace.publicState() });
    } else if (msg.kind === 'workspaceReconnect') {
      if (!state.connected || !state.workspaceSupported)
        throw new Error('Connect the JevRev app before reconnecting a workspace.');
      await workspace.reconnect(msg.groupId, msg.tabIds);
      reply({ workspace: workspace.publicState() });
    } else if (msg.kind === 'workspaceSelect') {
      abortOperations();
      // A manual target change invalidates the task, unlike an agent-authorized switch.
      if (!workspace.value) throw new Error('Create a workspace first.');
      workspace.value = { ...workspace.value, revision: crypto.randomUUID() };
      await workspace.publish();
      await workspace.select(msg.tabId, {
        ...scope(),
        expected_tab: attachedTab,
        expected_attachment: attachmentId,
      });
      reply({ workspace: workspace.publicState() });
    } else if (msg.kind === 'workspaceRemove') {
      abortOperations();
      await workspace.remove(msg.tabId);
      reply({ workspace: workspace.publicState() });
    } else if (msg.kind === 'workspaceDetach') {
      abortOperations();
      await workspace.detach();
      reply({ detached: true });
    } else if (msg.kind === 'attach') {
      if (msg.on && (!Number.isInteger(msg.tabId) || msg.tabId < 0))
        throw new Error('Choose the website tab you want to attach.');
      if (msg.on) {
        await ensureContent(msg.tabId);
      }
      abortOperations();
      if (workspace.value) await workspace.detach();
      attachedTab = msg.on ? msg.tabId : null;
      attachmentId = attachedTab == null ? '' : crypto.randomUUID();
      await chrome.storage.session.set({ tab: attachedTab, attachmentId });
      state.tab = attachedTab;
      await announce();
      badge(
        state.connected ? (attachedTab != null ? 'ON' : '·') : 'OFF',
        state.connected ? '#1f8a4c' : '#c2372f',
      );
      reply({ tab: attachedTab });
    } else if (msg.kind === 'openDemo') {
      const demo = new URL(state.url);
      demo.protocol = 'http:';
      demo.pathname = '/browser-demo.html';
      const tab = await chrome.tabs.create({ url: demo.href });
      reply({ tab: tab.id });
    } else if (msg.kind === 'pair') {
      const code = String(msg.code || '')
        .toUpperCase()
        .replace(/[^A-Z0-9]/g, '');
      if (code.length !== 8) throw new Error('Enter the 8-character code shown by PANINI.');
      // Pairing is only ever redeemed with the local app at the configured address.
      const endpoint = new URL(localUrl(state.url));
      endpoint.protocol = 'http:';
      endpoint.pathname = '/api/extension/pair';
      let response;
      try {
        response = await fetch(endpoint.href, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ code, label: 'Chrome' }),
        });
      } catch {
        throw new Error(`PANINI is not reachable at ${endpoint.host}. Start PANINI and try again.`);
      }
      const body = await response.json().catch(() => ({}));
      if (!response.ok)
        throw new Error(
          body.detail || 'The code is incorrect or expired. Create a new code in PANINI.',
        );
      if (!/^[a-f0-9]{32}$/.test(body.device_id) || !/^[a-f0-9]{64}$/.test(body.secret))
        throw new Error('PANINI returned an invalid pairing. Update PANINI and try again.');
      pairing = { device_id: body.device_id, secret: body.secret };
      await chrome.storage.local.set({ pairing });
      reconnect();
      reply({ paired: true });
    } else if (msg.kind === 'forget') {
      pairing = null;
      await chrome.storage.local.remove('pairing');
      reconnect();
      reply({ paired: false });
    } else if (msg.kind === 'setUrl' || msg.kind === 'reconnect') {
      state.url = localUrl(msg.url);
      await chrome.storage.local.set({ url: state.url });
      reconnect();
      reply({ url: state.url });
    }
  })().catch((error) => reply({ error: error.message }));
  return true; // reply arrives asynchronously
});

chrome.tabs.onRemoved.addListener((id) => {
  if (workspace.value) {
    workspace.reconcile().catch(report);
    return;
  }
  if (id === attachedTab) {
    attachedTab = null;
    attachmentId = '';
    state.tab = null;
    chrome.storage.session.remove(['tab', 'attachmentId']);
    badge('·', '#3b5bdb');
    announce();
  }
});

chrome.tabs.onUpdated.addListener((id, info) => {
  if (workspace.value) {
    workspace.reconcile().catch(report);
    return;
  }
  if (id === attachedTab && (info.url || info.title || info.status === 'complete')) announce();
});

function report(error) {
  state.lastError = error.message;
}
chrome.tabs.onDetached?.addListener(() => workspace.reconcile().catch(report));
chrome.tabs.onReplaced?.addListener(() => workspace.reconcile().catch(report));
chrome.tabGroups?.onRemoved.addListener(() => workspace.reconcile().catch(report));
chrome.permissions?.onRemoved?.addListener((permissions) => {
  if (workspace.value && permissions.permissions?.includes('tabGroups')) {
    abortOperations();
    workspace.detach().catch(report);
  }
});
const ready = load();
ready
  .then(() => {
    badge('OFF', '#c2372f');
    connect();
  })
  .catch(report);
setInterval(() => {
  if (!state.connected) connect();
  else announce();
}, 20000); // WebSocket traffic also keeps an attached MV3 worker available.
