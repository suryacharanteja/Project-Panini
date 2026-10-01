import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  TabWorkspace,
  website,
} from '../../panini-core/capabilities/browser/extension/workspace.mjs';

function fixture() {
  const tabs = new Map(
    [1, 2, 3].map((id) => [
      id,
      { id, windowId: 1, groupId: -1, url: `https://example.com/${id}`, title: `Page ${id}` },
    ]),
  );
  let permitted = true;
  const groups = new Map();
  const changes = [];
  const chrome = {
    permissions: { contains: async () => permitted },
    tabs: {
      get: async (id) => {
        if (!tabs.has(id)) throw new Error('Tab closed');
        return { ...tabs.get(id) };
      },
      group: async ({ tabIds, groupId = 90 }) => {
        for (const id of tabIds) tabs.get(id).groupId = groupId;
        groups.set(groupId, {
          id: groupId,
          windowId: tabs.get(tabIds[0]).windowId,
          title: 'JevRev',
        });
        return groupId;
      },
      query: async ({ groupId }) => [...tabs.values()].filter((tab) => tab.groupId === groupId),
      ungroup: async (ids) => {
        for (const id of ids) tabs.get(id).groupId = -1;
      },
      update: async (id, value) => {
        Object.assign(tabs.get(id), value);
        return tabs.get(id);
      },
      create: async (value) => {
        const tab = { id: 10, groupId: -1, ...value };
        tabs.set(10, tab);
        return tab;
      },
      remove: async (id) => tabs.delete(id),
    },
    tabGroups: {
      update: async () => {},
      get: async (id) => {
        if (!groups.has(id)) throw new Error('Group closed');
        return groups.get(id);
      },
      query: async ({ title }) => [...groups.values()].filter((group) => group.title === title),
    },
  };
  const workspace = new TabWorkspace(chrome, async (value) => changes.push(structuredClone(value)));
  const action = () => ({
    expected_workspace: workspace.value.id,
    expected_revision: workspace.value.revision,
    expected_tab: workspace.current().id,
    expected_attachment: workspace.current().grant,
  });
  return { tabs, groups, chrome, workspace, action, changes, deny: () => (permitted = false) };
}

test('lost session can reconnect an existing group without creating, moving or reopening tabs', async () => {
  const f = fixture();
  await f.workspace.create([1, 2], true);
  const old = f.action();
  // Reloading the extension loses session storage, but Chrome keeps the group.
  const restarted = new TabWorkspace(f.chrome);
  const [available] = await restarted.existing();
  assert.equal(available.group_id, 90);
  assert.deepEqual(
    available.tabs.map((tab) => tab.id),
    [1, 2],
  );
  f.chrome.tabs.group =
    f.chrome.tabs.ungroup =
    f.chrome.tabs.create =
      async () => assert.fail('Must reuse the existing group');
  f.tabs.get(2).active = true;
  await restarted.reconnect(
    90,
    available.tabs.map((tab) => tab.id),
  );
  assert.equal(restarted.value.group_id, 90);
  assert.equal(restarted.current().id, 2);
  assert.equal(restarted.value.allow_new_tabs, false);
  assert.equal(f.tabs.get(3).groupId, -1);
  await assert.rejects(restarted.validate(old), /changed/);
});

test('reconnecting the active workspace preserves the task grant and permissions', async () => {
  const f = fixture();
  await f.workspace.create([1, 2], true);
  const old = f.action();
  const value = f.workspace.value;
  const changes = f.changes.length;
  await f.workspace.reconnect(90, [1, 2]);
  assert.equal(f.workspace.value, value);
  assert.equal(f.changes.length, changes);
  assert.equal(await f.workspace.validate(old), 1);
  assert.equal(f.workspace.value.allow_new_tabs, true);
});

test('reconnection approves only reviewed members and rejects changed or unrelated groups', async () => {
  const f = fixture();
  await f.workspace.create([1, 2]);
  await f.workspace.detach();
  const [shown] = await f.workspace.existing();
  f.tabs.get(3).groupId = 90; // Added after the reconnect view was shown.
  await f.workspace.reconnect(
    90,
    shown.tabs.map((tab) => tab.id),
  );
  assert.deepEqual(
    f.workspace.value.tabs.map((tab) => tab.id),
    [1, 2],
  );
  await assert.rejects(f.workspace.select(3, f.action()), /not approved/);
  await f.workspace.detach();
  f.tabs.get(2).groupId = -1;
  await assert.rejects(f.workspace.reconnect(90, [1, 2]), /changed/);
  await assert.rejects(f.workspace.reconnect(90, [1, 1]), /Choose/);
  await assert.rejects(f.workspace.reconnect(90, []), /Choose/);
  f.groups.get(90).title = 'Personal';
  assert.deepEqual(await f.workspace.existing(), []);
  await assert.rejects(f.workspace.reconnect(90, [1]), /no longer open/);
  f.groups.delete(90);
  await assert.rejects(f.workspace.reconnect(90, [1]), /no longer open/);
  assert.equal(f.workspace.value, null);
});

test('discovery excludes restricted pages and reconnect honors revoked permission', async () => {
  const f = fixture();
  await f.workspace.create([1, 2]);
  await f.workspace.detach();
  f.tabs.get(2).url = 'chrome://settings';
  assert.deepEqual(
    (await f.workspace.existing())[0].tabs.map((tab) => tab.id),
    [1],
  );
  await assert.rejects(f.workspace.reconnect(90, [1, 2]), /changed/);
  f.deny();
  assert.deepEqual(await f.workspace.existing(), []);
  await assert.rejects(f.workspace.reconnect(90, [1]), /Allow tab groups/);
});

test('workspace only selects explicitly approved tabs; agent switches retain the task grant', async () => {
  const f = fixture();
  await f.workspace.create([1, 2]);
  const scope = f.workspace.value.id;
  const revision = f.workspace.value.revision;
  // Visual group membership alone is not authority.
  f.tabs.get(3).groupId = 90;
  await assert.rejects(f.workspace.select(3, f.action()), /not approved/);
  const old = f.action();
  await f.workspace.select(2, old);
  assert.equal(f.workspace.current().id, 2);
  assert.equal(f.workspace.value.id, scope);
  assert.equal(f.workspace.value.revision, revision);
  await assert.rejects(f.workspace.validate(old), /changed/);
  assert.equal(await f.workspace.validate(f.action()), 2);
});

test('new tabs require opt-in, get grouped, and become the selected target', async () => {
  const f = fixture();
  await f.workspace.create([1]);
  await assert.rejects(f.workspace.open('https://example.com/new', f.action()), /disabled/);
  await f.workspace.create([1], true);
  const revision = f.workspace.value.revision;
  await f.workspace.open('https://example.com/new', f.action());
  assert.equal(f.tabs.get(10).groupId, 90);
  assert.equal(f.workspace.current().id, 10);
  assert.equal(f.workspace.value.revision, revision);
  assert.equal(await f.workspace.validate(f.action()), 10);
});

test('cross-window, restricted, duplicate and other-group selections are rejected before mutation', async () => {
  const f = fixture();
  f.tabs.get(2).windowId = 2;
  await assert.rejects(f.workspace.create([1, 2]), /one Chrome window/);
  f.tabs.get(2).windowId = 1;
  f.tabs.get(2).groupId = 99;
  await assert.rejects(f.workspace.create([1, 2]), /Ungroup/);
  f.tabs.get(2).url = 'chrome://extensions/';
  await assert.rejects(f.workspace.create([1, 2]), /website/);
  await assert.rejects(f.workspace.create([1, 1]), /Choose/);
  assert.equal(f.tabs.get(1).groupId, -1);
  assert.equal(f.workspace.value, null);
});

test('moving an approved tab out revokes it without adopting tabs dragged in', async () => {
  const f = fixture();
  await f.workspace.create([1, 2]);
  const old = f.action();
  f.tabs.get(1).groupId = -1;
  f.tabs.get(3).groupId = 90;
  await f.workspace.reconcile();
  assert.deepEqual(
    f.workspace.value.tabs.map((t) => t.id),
    [2],
  );
  assert.equal(f.workspace.current().id, 2);
  await assert.rejects(f.workspace.validate(old), /changed/);
  assert.equal(f.tabs.get(1).url, 'https://example.com/1');
});

test('closed last tab ends workspace; detach keeps pages and removes authority', async () => {
  const f = fixture();
  await f.workspace.create([1]);
  f.tabs.delete(1);
  await f.workspace.reconcile();
  assert.equal(f.workspace.value, null);
  await f.workspace.create([2]);
  const old = f.action();
  await f.workspace.detach();
  await assert.rejects(f.workspace.validate(old), /changed/);
  assert.ok(f.tabs.has(2));
});

test('worker restore reissues grants and excludes moved or closed members', async () => {
  const f = fixture();
  await f.workspace.create([1, 2]);
  const old = f.action();
  const saved = structuredClone(f.workspace.value);
  f.tabs.get(2).windowId = 2;
  await f.workspace.restore(saved);
  assert.deepEqual(
    f.workspace.value.tabs.map((t) => t.id),
    [1],
  );
  await assert.rejects(f.workspace.validate(old), /changed/);
  assert.equal(await f.workspace.validate(f.action()), 1);
});

test('permission rejection preserves the single-tab choice; failed group decoration rolls back grouping', async () => {
  const f = fixture();
  f.chrome.tabGroups.update = async () => {
    throw new Error('Group unavailable');
  };
  await assert.rejects(f.workspace.create([1, 2]), /Group unavailable/);
  assert.equal(f.tabs.get(1).groupId, -1);
  assert.equal(f.workspace.value, null);
  f.deny();
  await assert.rejects(f.workspace.create([1]), /Allow tab groups/);
});

test('Stop during tab creation removes only the uncommitted new tab', async () => {
  const f = fixture();
  await f.workspace.create([1], true);
  let checks = 0;
  await assert.rejects(
    f.workspace.open('https://example.com/new', f.action(), () => {
      if (++checks > 1) throw new Error('Cancelled');
    }),
    /Cancelled/,
  );
  assert.equal(f.tabs.has(10), false);
  assert.equal(f.workspace.current().id, 1);
  assert.equal(f.tabs.has(1), true);
});

test('scope revoked during tab creation cannot navigate the new tab', async () => {
  const f = fixture();
  await f.workspace.create([1], true);
  const create = f.chrome.tabs.create;
  f.chrome.tabs.create = async (value) => {
    const tab = await create(value);
    await f.workspace.detach();
    return tab;
  };
  await assert.rejects(f.workspace.open('https://example.com/new', f.action()), /changed/);
  assert.equal(f.tabs.has(10), false);
});

test('page updates refresh metadata without invalidating workspace authority', async () => {
  const f = fixture();
  await f.workspace.create([1, 2]);
  const old = f.action();
  f.tabs.get(1).url = 'https://example.com/results';
  await f.workspace.reconcile();
  assert.equal(await f.workspace.validate(old), 1);
  assert.equal(f.workspace.current().url, 'https://example.com/results');
  assert.equal('grant' in f.workspace.publicState().tabs[0], false);
});

test('restricted URLs and credential-bearing URLs are never opened', async () => {
  const f = fixture();
  await f.workspace.create([1], true);
  for (const url of [
    'chrome://settings',
    'javascript:alert(1)',
    'file:///secret',
    'https://user:pass@example.com',
    'https://chromewebstore.google.com/',
  ]) {
    assert.equal(website(url), false);
    await assert.rejects(f.workspace.open(url, f.action()), /HTTP/);
  }
  assert.equal(f.tabs.has(10), false);
});
