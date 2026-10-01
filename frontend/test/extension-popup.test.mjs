import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

test('popup picks website tabs from a restricted page and retains errors and edits across polling', async () => {
  const elements = new Map();
  const el = (id) => {
    if (!elements.has(id))
      elements.set(id, {
        value: '',
        textContent: '',
        replaceChildren(...nodes) {
          this.children = nodes;
        },
      });
    return elements.get(id);
  };
  const sent = [];
  const context = vm.createContext({
    document: { getElementById: el },
    URL,
    Option: class {
      constructor(text, value) {
        this.text = text;
        this.value = value;
      }
    },
    setInterval() {},
    chrome: {
      runtime: {
        sendMessage(msg, reply) {
          sent.push(msg);
          reply(
            msg.kind === 'state'
              ? {
                  connected: true,
                  url: 'ws://localhost:3000/ws/extension',
                  tab: null,
                  currentSupported: false,
                  currentTab: 8,
                  tabs: [{ id: 7, title: 'My site', url: 'https://example.com/' }],
                }
              : { error: 'Website permission was denied' },
          );
        },
      },
    },
  });
  vm.runInContext(
    readFileSync(
      new URL('../../panini-core/capabilities/browser/extension/popup.js', import.meta.url),
      'utf8',
    ),
    context,
  );
  await new Promise((resolve) => setImmediate(resolve));
  assert.match(el('hint').textContent, /Chrome settings/);
  assert.equal(el('attach').disabled, true);
  el('tabs').value = '7';
  el('tabs').onchange();
  assert.equal(el('attach').disabled, false);
  await el('attach').onclick();
  assert.equal(sent.find((s) => s.kind === 'attach').tabId, 7);
  el('url').value = 'ws://localhost:3200/ws/extension';
  await vm.runInContext('refresh()', context);
  assert.match(el('error').textContent, /permission was denied/);
  assert.equal(el('tabs').value, '7');
  assert.equal(el('url').value, 'ws://localhost:3200/ws/extension');
  assert.equal(el('attach').disabled, false);
});

test('workspace popup reflects agent-added tabs, preserves edits, and handles permission denial', async () => {
  const elements = new Map();
  const el = (id) => {
    if (!elements.has(id))
      elements.set(id, {
        value: '',
        options: [],
        textContent: '',
        replaceChildren(...nodes) {
          this.options = nodes;
        },
        get selectedOptions() {
          return this.options.filter((o) => o.selected);
        },
      });
    return elements.get(id);
  };
  const tabs = [7, 8, 9].map((id) => ({
    id,
    title: `Page ${id}`,
    url: `https://example.com/${id}`,
    windowId: 1,
    groupId: -1,
  }));
  const state = {
    connected: true,
    workspaceSupported: true,
    url: 'ws://localhost:3100/ws/extension',
    tab: 7,
    tabs,
    currentTab: 7,
    currentSupported: true,
    workspace: {
      id: 'w1',
      selected_tab: 7,
      group_id: 5,
      allow_new_tabs: false,
      tabs: tabs.slice(0, 2),
    },
  };
  const sent = [];
  const context = vm.createContext({
    document: { getElementById: el },
    URL,
    setInterval() {},
    Option: class {
      constructor(text, value) {
        this.text = text;
        this.value = value;
        this.selected = false;
      }
    },
    chrome: {
      runtime: {
        getManifest: () => ({ version: '2.0.0' }),
        sendMessage(msg, reply) {
          sent.push(msg);
          reply(state);
        },
      },
      permissions: { request: async () => false },
    },
  });
  vm.runInContext(
    readFileSync(
      new URL('../../panini-core/capabilities/browser/extension/popup.js', import.meta.url),
      'utf8',
    ),
    context,
  );
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(el('mode').value, 'workspace');
  assert.equal(el('workspace-setup').open, false);
  assert.deepEqual(
    el('workspace-tabs').selectedOptions.map((o) => o.value),
    ['7', '8'],
  );
  state.workspace.tabs.push(tabs[2]);
  await vm.runInContext('refresh()', context);
  assert.deepEqual(
    el('workspace-tabs').selectedOptions.map((o) => o.value),
    ['7', '8', '9'],
  );
  el('workspace-tabs').options.find((o) => o.value === '8').selected = false;
  el('workspace-tabs').onchange();
  tabs[0].title = 'A changed title';
  await vm.runInContext('refresh()', context);
  assert.deepEqual(
    el('workspace-tabs').selectedOptions.map((o) => o.value),
    ['7', '9'],
  );
  await el('workspace-save').onclick();
  assert.match(el('error').textContent, /permission was not granted/);
  assert.equal(
    sent.some((msg) => msg.kind === 'workspaceCreate'),
    false,
  );
  assert.equal(el('workspace-save').disabled, false);
});

test('popup reconnects an existing group after session loss and mode changes keep the active workspace', async () => {
  const elements = new Map();
  const el = (id) => {
    if (!elements.has(id))
      elements.set(id, {
        value: '',
        options: [],
        textContent: '',
        replaceChildren(...nodes) {
          this.options = nodes;
        },
        get selectedOptions() {
          return this.options.filter((option) => option.selected);
        },
      });
    return elements.get(id);
  };
  const tabs = [7, 8].map((id) => ({
    id,
    title: `Page ${id}`,
    url: `https://example.com/${id}`,
    windowId: 2,
    groupId: 5,
  }));
  const state = {
    connected: true,
    workspaceSupported: true,
    tab: null,
    workspace: null,
    url: 'ws://localhost:3100/ws/extension',
    tabs,
    currentTab: 7,
    currentSupported: true,
    workspaces: [{ group_id: 5, window_id: 2, tabs }],
  };
  const sent = [];
  const context = vm.createContext({
    document: { getElementById: el, createElement: () => ({ textContent: '' }) },
    URL,
    setInterval() {},
    Option: class {
      constructor(text, value) {
        Object.assign(this, { text, value, selected: false });
      }
    },
    chrome: {
      runtime: {
        getManifest: () => ({ version: '2.0.2' }),
        sendMessage(msg, reply) {
          sent.push(msg);
          if (msg.kind === 'workspaceReconnect') {
            state.workspace = {
              id: 'restored',
              group_id: 5,
              selected_tab: 7,
              allow_new_tabs: false,
              tabs,
            };
            state.tab = 7;
          }
          reply(state);
        },
      },
      permissions: {
        request: async () => assert.fail('Reconnection needs no new group permission'),
      },
    },
  });
  vm.runInContext(
    readFileSync(
      new URL('../../panini-core/capabilities/browser/extension/popup.js', import.meta.url),
      'utf8',
    ),
    context,
  );
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(el('mode').value, 'workspace');
  assert.equal(el('workspace-setup').open, false);
  assert.equal(el('workspace-existing').hidden, false);
  assert.equal(el('workspace-group').value, '5');
  assert.equal(el('workspace-reconnect').disabled, false);
  assert.equal(el('workspace-reconnect-tabs').options.length, 2);
  assert.match(el('workspace-setup-label').textContent, /different workspace/);
  await el('workspace-reconnect').onclick();
  const request = sent.find((msg) => msg.kind === 'workspaceReconnect');
  assert.equal(request.groupId, 5);
  assert.deepEqual(Array.from(request.tabIds), [7, 8]);
  assert.equal(
    sent.some((msg) => msg.kind === 'workspaceCreate'),
    false,
  );
  assert.equal(el('workspace-existing').hidden, true);
  assert.equal(el('workspace-reconnect').disabled, true);
  assert.equal(el('workspace-active').hidden, false);
  assert.match(el('workspace-summary').textContent, /Workspace connected/);
  assert.equal(el('workspace-save').textContent, 'Update approved tabs');
  assert.ok(el('workspace-tabs').selectedOptions.every((option) => !option.disabled));
  const before = sent.length;
  el('mode').value = 'single';
  el('mode').onchange();
  el('mode').value = 'workspace';
  el('mode').onchange();
  assert.equal(sent.length, before, 'Choosing a mode must not recreate or detach a workspace');
  assert.equal(el('workspace-active').hidden, false);
});
