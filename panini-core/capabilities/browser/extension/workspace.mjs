// The group is presentation; this session registry is the authority to act.
export const MAX_TABS = 24;

export function website(value) {
  try {
    const url = new URL(value);
    return (
      ['http:', 'https:'].includes(url.protocol) &&
      !url.username &&
      !url.password &&
      url.hostname !== 'chromewebstore.google.com' &&
      !(url.hostname === 'chrome.google.com' && url.pathname.startsWith('/webstore'))
    );
  } catch {
    return false;
  }
}

export class TabWorkspace {
  constructor(chrome, changed = async () => {}) {
    this.chrome = chrome;
    this.changed = changed;
    this.value = null;
  }

  async publish() {
    await this.changed(this.value);
  }

  async existing() {
    if (!(await this.chrome.permissions?.contains({ permissions: ['tabGroups'] }))) return [];
    const groups = await this.chrome.tabGroups.query({ title: 'JevRev' });
    const found = await Promise.all(
      groups.map(async (group) => {
        const tabs = (await this.chrome.tabs.query({ groupId: group.id }))
          .filter((tab) => website(tab.url) && tab.windowId === group.windowId)
          .map((tab) => ({ id: tab.id, title: tab.title || tab.url, url: tab.url }));
        return { group_id: group.id, window_id: group.windowId, tabs };
      }),
    );
    return found.filter((group) => group.tabs.length);
  }

  async reconnect(groupId, ids) {
    // Reopening an already connected workspace must not rotate its grants or stop its task.
    if (this.value?.group_id === groupId) return this.value;
    if (!Number.isInteger(groupId) || groupId < 0)
      throw new Error('Choose an existing JevRev workspace.');
    if (
      !Array.isArray(ids) ||
      !ids.length ||
      ids.length > MAX_TABS ||
      ids.some((id) => !Number.isInteger(id) || id < 0) ||
      new Set(ids).size !== ids.length
    )
      throw new Error(`Choose between 1 and ${MAX_TABS} website tabs.`);
    if (!(await this.chrome.permissions.contains({ permissions: ['tabGroups'] })))
      throw new Error('Allow tab groups before reconnecting a workspace.');
    const group = await this.chrome.tabGroups.get(groupId).catch(() => null);
    if (!group || group.title !== 'JevRev')
      throw new Error('That JevRev workspace is no longer open. Choose an available workspace.');
    const tabs = await Promise.all(ids.map((id) => this.chrome.tabs.get(id).catch(() => null)));
    if (
      tabs.some(
        (tab) =>
          !tab || !website(tab.url) || tab.groupId !== groupId || tab.windowId !== group.windowId,
      )
    )
      throw new Error('Workspace tabs changed. Review the available tabs and reconnect.');
    // Only the tabs displayed when the user clicked Reconnect are approved. A group
    // title or a newly dragged-in tab is never enough to authorize an action.
    this.value = {
      id: crypto.randomUUID(),
      revision: crypto.randomUUID(),
      window_id: group.windowId,
      group_id: groupId,
      selected_tab: tabs.find((tab) => tab.active)?.id ?? ids[0],
      allow_new_tabs: false,
      tabs: tabs.map((tab) => ({
        id: tab.id,
        grant: crypto.randomUUID(),
        title: tab.title || '',
        url: tab.url,
      })),
    };
    await this.publish();
    return this.value;
  }

  async create(ids, allowNew = false) {
    if (!(await this.chrome.permissions.contains({ permissions: ['tabGroups'] })))
      throw new Error('Allow tab groups to create a workspace.');
    if (
      !Array.isArray(ids) ||
      !ids.length ||
      ids.length > MAX_TABS ||
      ids.some((id) => !Number.isInteger(id) || id < 0) ||
      new Set(ids).size !== ids.length
    )
      throw new Error(`Choose between 1 and ${MAX_TABS} website tabs.`);
    const tabs = await Promise.all(ids.map((id) => this.chrome.tabs.get(id)));
    if (tabs.some((tab) => !website(tab.url)))
      throw new Error('Only website tabs can join a workspace.');
    if (tabs.some((tab) => tab.windowId !== tabs[0].windowId))
      throw new Error('Choose tabs from one Chrome window.');
    // Never take tabs out of another group without a separate explicit user move.
    if (tabs.some((tab) => tab.groupId >= 0 && tab.groupId !== this.value?.group_id))
      throw new Error('Ungroup tabs from other groups before adding them to JevRev.');
    const previous = this.value;
    if (previous && previous.window_id !== tabs[0].windowId)
      throw new Error('Detach this workspace before choosing another window.');
    const groupId = await this.chrome.tabs.group({
      tabIds: ids,
      ...(previous ? { groupId: previous.group_id } : {}),
    });
    try {
      await this.chrome.tabGroups.update(groupId, {
        title: 'JevRev',
        color: 'yellow',
        collapsed: false,
      });
    } catch (error) {
      // Restore newly grouped tabs on an unsuccessful creation; never close pages.
      await this.chrome.tabs
        .ungroup(tabs.filter((t) => t.groupId !== groupId).map((t) => t.id))
        .catch(() => {});
      throw error;
    }
    this.value = {
      id: crypto.randomUUID(),
      revision: crypto.randomUUID(),
      window_id: tabs[0].windowId,
      group_id: groupId,
      selected_tab: ids.includes(previous?.selected_tab) ? previous.selected_tab : ids[0],
      allow_new_tabs: allowNew === true,
      tabs: tabs.map((tab) => ({
        id: tab.id,
        grant: crypto.randomUUID(),
        title: tab.title || '',
        url: tab.url,
      })),
    };
    await this.publish();
    for (const member of previous?.tabs || []) {
      if (!ids.includes(member.id)) {
        const tab = await this.chrome.tabs.get(member.id).catch(() => null);
        if (tab?.groupId === groupId) await this.chrome.tabs.ungroup([member.id]);
      }
    }
    return this.value;
  }

  async restore(saved) {
    if (
      !saved ||
      !Array.isArray(saved.tabs) ||
      !saved.tabs.length ||
      saved.tabs.length > MAX_TABS ||
      !Number.isInteger(saved.group_id) ||
      !Number.isInteger(saved.window_id) ||
      !(await this.chrome.permissions.contains({ permissions: ['tabGroups'] }))
    )
      return;
    const tabs = [];
    for (const member of saved.tabs) {
      const tab = await this.chrome.tabs.get(member.id).catch(() => null);
      if (
        tab &&
        website(tab.url) &&
        tab.windowId === saved.window_id &&
        tab.groupId === saved.group_id
      )
        tabs.push({ id: tab.id, title: tab.title || '', url: tab.url, grant: crypto.randomUUID() });
    }
    if (!tabs.length) return;
    this.value = {
      ...saved,
      id: crypto.randomUUID(),
      revision: crypto.randomUUID(),
      tabs,
      selected_tab: tabs.some((t) => t.id === saved.selected_tab) ? saved.selected_tab : tabs[0].id,
    };
    await this.publish();
  }

  current() {
    return this.value?.tabs.find((tab) => tab.id === this.value.selected_tab);
  }

  publicState() {
    const value = this.value;
    return value ? { ...value, tabs: value.tabs.map(({ grant: _grant, ...tab }) => tab) } : null;
  }

  async validate(action) {
    const value = this.value;
    const member = this.current();
    if (
      !value ||
      action.expected_workspace !== value.id ||
      action.expected_revision !== value.revision ||
      action.expected_tab !== member?.id ||
      action.expected_attachment !== member?.grant
    )
      throw new Error('Workspace changed. Review the attached tabs and run again.');
    const tab = await this.chrome.tabs.get(member.id).catch(() => null);
    if (
      !tab ||
      tab.groupId !== value.group_id ||
      tab.windowId !== value.window_id ||
      !website(tab.url)
    ) {
      await this.reconcile();
      throw new Error('The target left the approved workspace. Review your tabs and run again.');
    }
    if (
      this.value?.id !== value.id ||
      this.value?.revision !== value.revision ||
      this.current()?.id !== member.id ||
      this.current()?.grant !== member.grant
    )
      throw new Error('Workspace changed. Review the attached tabs and run again.');
    return tab.id;
  }

  async select(id, action, check = () => {}) {
    await this.validate(action);
    const value = this.value;
    const member = value.tabs.find((tab) => tab.id === id);
    if (!member) throw new Error('That tab is not approved for this workspace.');
    const tab = await this.chrome.tabs.get(id);
    if (tab.groupId !== value.group_id || tab.windowId !== value.window_id || !website(tab.url))
      throw new Error('That tab left the workspace.');
    check();
    await this.validate(action);
    check();
    await this.chrome.tabs.update(id, { active: true });
    check();
    await this.validate(action);
    this.value = { ...this.value, selected_tab: id };
    await this.publish();
    return { tab_id: id, url: tab.url, title: tab.title };
  }

  async open(url, action, check = () => {}) {
    await this.validate(action);
    const value = this.value;
    if (!value.allow_new_tabs) throw new Error('Opening tabs is disabled for this workspace.');
    if (value.tabs.length >= MAX_TABS)
      throw new Error(`Workspace is limited to ${MAX_TABS} tabs. Detach a tab first.`);
    if (!website(url)) throw new Error('Only HTTP(S) website addresses can be opened.');
    check();
    // Create blank first so no website executes before membership is established.
    const tab = await this.chrome.tabs.create({
      url: 'about:blank',
      windowId: value.window_id,
      active: false,
    });
    let committed = false;
    try {
      check();
      await this.validate(action);
      await this.chrome.tabs.group({ tabIds: [tab.id], groupId: value.group_id });
      check();
      await this.validate(action);
      await this.chrome.tabs.update(tab.id, { url, active: true });
      // tabs.update can return while the newly created document is still blank.
      // Do not publish a member that reconciliation would immediately revoke.
      const deadline = Date.now() + 15000;
      while (!website((await this.chrome.tabs.get(tab.id)).url)) {
        check();
        await this.validate(action);
        if (Date.now() >= deadline)
          throw new Error('The new tab did not load a website. Try again.');
        await new Promise((resolve) => setTimeout(resolve, 100));
      }
      check();
      await this.validate(action);
      this.value = {
        ...this.value,
        selected_tab: tab.id,
        tabs: [...this.value.tabs, { id: tab.id, grant: crypto.randomUUID(), title: '', url }],
      };
      committed = true;
      await this.publish();
      return { tab_id: tab.id, url };
    } finally {
      if (!committed) await this.chrome.tabs.remove(tab.id).catch(() => {});
    }
  }

  async reconcile() {
    const value = this.value;
    if (!value) return;
    const tabs = [];
    for (const member of value.tabs) {
      const tab = await this.chrome.tabs.get(member.id).catch(() => null);
      if (
        tab &&
        tab.windowId === value.window_id &&
        tab.groupId === value.group_id &&
        website(tab.url)
      )
        tabs.push({ ...member, title: tab.title || '', url: tab.url });
    }
    if (this.value !== value) return;
    const revoked = tabs.length !== value.tabs.length;
    if (
      !revoked &&
      tabs.every((t, i) => t.title === value.tabs[i].title && t.url === value.tabs[i].url)
    )
      return;
    this.value = tabs.length
      ? {
          ...value,
          tabs,
          revision: revoked ? crypto.randomUUID() : value.revision,
          selected_tab: tabs.some((t) => t.id === value.selected_tab)
            ? value.selected_tab
            : tabs[0].id,
        }
      : null;
    await this.publish();
  }

  async remove(id) {
    const value = this.value;
    if (!value?.tabs.some((t) => t.id === id))
      throw new Error('That tab is not in this workspace.');
    const tabs = value.tabs.filter((t) => t.id !== id);
    this.value = tabs.length
      ? {
          ...value,
          tabs,
          revision: crypto.randomUUID(),
          selected_tab: id === value.selected_tab ? tabs[0].id : value.selected_tab,
        }
      : null;
    await this.publish(); // Revoke before touching the visible group.
    const tab = await this.chrome.tabs.get(id).catch(() => null);
    if (tab?.groupId === value.group_id) await this.chrome.tabs.ungroup([id]);
  }

  async detach() {
    this.value = null;
    await this.publish(); // Pages and the visual group remain open, without control.
  }
}
