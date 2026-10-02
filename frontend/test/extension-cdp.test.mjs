import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createCdp } from '../../panini-core/capabilities/browser/extension/cdp.mjs';
import { decide, TABLE } from '../../panini-core/capabilities/browser/extension/cdp-policy.mjs';
import { website } from '../../panini-core/capabilities/browser/extension/workspace.mjs';

test('the method policy keeps PANINI inside the attached tab (I9)', () => {
  for (const method of [
    'Network.getAllCookies',
    'Network.clearBrowserCookies',
    'Network.setCookie',
    'Storage.getCookies',
    'Storage.clearDataForOrigin',
    'Browser.close',
    'Target.createBrowserContext',
    'Debugger.enable',
    'Fetch.enable',
    'Totally.unknown',
    'Page.setBypassCSP',
  ])
    assert.equal(decide(method, {})[0], false, method);
  for (const method of [
    'Runtime.evaluate',
    'Input.dispatchMouseEvent',
    'DOM.describeNode',
    'IO.read',
  ])
    assert.equal(decide(method, {})[0], true, method);
  assert.equal(decide('Page.navigate', { url: 'https://example.com' })[0], true);
  assert.equal(decide('Page.navigate', { url: 'about:blank' })[0], true);
  for (const url of [
    'javascript:alert(1)',
    'file:///C:/x',
    'chrome://settings',
    'data:text/html,x',
  ])
    assert.equal(decide('Page.navigate', { url })[0], false, url);
  const scope = { origin: 'https://shop.example' };
  assert.equal(
    decide('Network.getCookies', { urls: ['https://shop.example/cart'] }, scope)[0],
    true,
  );
  assert.equal(
    decide(
      'Network.getCookies',
      { urls: ['https://shop.example/', 'https://bank.example/'] },
      scope,
    )[0],
    false,
  );
  assert.equal(decide('Network.getCookies', {}, scope)[0], false);
  assert.equal(decide('Fetch.enable', {}, { network_tools: true })[0], true);
  assert.ok(TABLE.deny_domains.includes('Storage'));
});

function fakeChrome(tabs) {
  const listeners = { event: [], detach: [] };
  const calls = [];
  let devtools = false;
  return {
    calls,
    listeners,
    openDevtools() {
      devtools = true;
    },
    chrome: {
      tabs: { get: async (id) => tabs.find((t) => t.id === id) },
      debugger: {
        onEvent: { addListener: (fn) => listeners.event.push(fn) },
        onDetach: { addListener: (fn) => listeners.detach.push(fn) },
        attach: async (target) => {
          if (devtools)
            throw new Error('Another debugger is already attached to the tab with id: 1.');
          calls.push(['attach', target.tabId]);
        },
        detach: async (target) => calls.push(['detach', target.tabId]),
        sendCommand: async (target, method, params) => {
          calls.push(['send', target.tabId, target.sessionId ?? null, method]);
          return method === 'Target.getTargetInfo'
            ? { targetInfo: { targetId: 'T1', type: 'page' } }
            : { ok: true };
        },
      },
    },
  };
}

test('full control attaches only the granted tab and polices every command', async () => {
  const tabs = [
    { id: 1, url: 'https://shop.example/cart' },
    { id: 2, url: 'https://bank.example/' },
    { id: 3, url: 'chrome://settings' },
  ];
  const fake = fakeChrome(tabs);
  const sent = [];
  let granted = 1;
  const target = async (action) => {
    if (action.expected_tab !== granted) throw new Error('Attached tab changed.');
    return granted;
  };
  const cdp = createCdp({ chrome: fake.chrome, send: (m) => sent.push(m), target, website });

  await assert.rejects(
    cdp.handle({ op: 'attach', tab_id: 2, expected_tab: 1 }),
    /not the one you attached/,
  );
  const attached = await cdp.handle({ op: 'attach', tab_id: 1, expected_tab: 1 });
  assert.deepEqual(attached.target_info, { targetId: 'T1', type: 'page' });

  await cdp.handle({
    op: 'send',
    tab_id: 1,
    expected_tab: 1,
    method: 'Runtime.evaluate',
    params: {},
  });
  await assert.rejects(
    cdp.handle({ op: 'send', tab_id: 1, expected_tab: 1, method: 'Network.getAllCookies' }),
    /not allowed/,
  );
  await assert.rejects(
    cdp.handle({
      op: 'send',
      tab_id: 1,
      expected_tab: 1,
      method: 'Network.getCookies',
      params: { urls: ['https://bank.example/'] },
    }),
    /outside the attached tab/,
  );
  await cdp.handle({
    op: 'send',
    tab_id: 1,
    expected_tab: 1,
    method: 'Network.getCookies',
    params: { urls: ['https://shop.example/x'] },
  });
  assert.ok(
    !fake.calls.some((c) => c[3] === 'Network.getAllCookies'),
    'denied commands never reach Chrome',
  );

  // Events: only for attached tabs.
  fake.listeners.event.forEach((fn) => fn({ tabId: 2 }, 'Page.loadEventFired', {}));
  fake.listeners.event.forEach((fn) =>
    fn({ tabId: 1, sessionId: 'child-1' }, 'Page.loadEventFired', {}),
  );
  assert.deepEqual(
    sent.map((m) => [m.type, m.tab_id, m.session_id]),
    [['cdp_event', 1, 'child-1']],
  );

  // The person changes the attached tab: the old grant fails the very next command.
  granted = 2;
  await assert.rejects(
    cdp.handle({ op: 'send', tab_id: 1, expected_tab: 1, method: 'Runtime.evaluate' }),
    /changed/,
  );

  // Cancel on Chrome's debugging bar: PANINI is told at once.
  fake.listeners.detach.forEach((fn) => fn({ tabId: 1 }, 'canceled_by_user'));
  assert.deepEqual(sent.at(-1), { type: 'cdp_detached', tab_id: 1, reason: 'canceled_by_user' });

  await assert.rejects(
    cdp.handle({ op: 'create_tab', url: 'https://x.example', expected_tab: 2 }),
    /Unsupported/,
  );
});

test('browser pages, DevTools and detach-all', async () => {
  const fake = fakeChrome([
    { id: 3, url: 'chrome://settings' },
    { id: 1, url: 'https://shop.example/' },
  ]);
  const target = async (action) => action.expected_tab;
  const cdp = createCdp({ chrome: fake.chrome, send() {}, target, website });
  await assert.rejects(
    cdp.handle({ op: 'attach', tab_id: 3, expected_tab: 3 }),
    /cannot be controlled/,
  );
  fake.openDevtools();
  await assert.rejects(cdp.handle({ op: 'attach', tab_id: 1, expected_tab: 1 }), /DEVTOOLS_OPEN/);
  const ok = fakeChrome([{ id: 1, url: 'https://shop.example/' }]);
  const second = createCdp({ chrome: ok.chrome, send() {}, target, website });
  await second.handle({ op: 'attach', tab_id: 1, expected_tab: 1 });
  await second.detachAll();
  assert.deepEqual(ok.calls.at(-1), ['detach', 1]);
  assert.equal(second.attached.size, 0);
});
