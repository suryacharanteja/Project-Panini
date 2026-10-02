const $ = (id) => document.getElementById(id);
const ask = (msg) =>
  new Promise((resolve, reject) => {
    chrome.runtime.sendMessage(msg, (result) => {
      const error = chrome.runtime.lastError?.message || result?.error;
      if (error) reject(new Error(error));
      else if (!result) reject(new Error('Extension did not respond. Reload it and try again.'));
      else resolve(result);
    });
  });

let busy = false;
let initialized = false;
let attachedTab = null;
let tabSignature = '';
let latest = null;
let workspaceSignature = '';
let workspaceId = null;
let workspaceDirty = false;
let groupSignature = '';

function existingGroup() {
  return latest?.workspaces?.find((group) => String(group.group_id) === $('workspace-group').value);
}

function renderExisting() {
  const groups = latest?.workspaces || [];
  const signature = JSON.stringify(groups);
  if (signature !== groupSignature) {
    const selected = $('workspace-group').value;
    const windows = [...new Set((latest?.tabs || []).map((tab) => tab.windowId))];
    $('workspace-group').replaceChildren(
      ...groups.map(
        (group) =>
          new Option(
            `JevRev · Window ${windows.indexOf(group.window_id) + 1} · ${group.tabs.length} tabs`,
            String(group.group_id),
          ),
      ),
    );
    const fallback =
      groups.find((group) => group.group_id === latest.workspace?.group_id) || groups[0];
    $('workspace-group').value = groups.some((group) => String(group.group_id) === selected)
      ? selected
      : String(fallback?.group_id ?? '');
    groupSignature = signature;
  }
  const group = existingGroup();
  const active = !!group && group.group_id === latest?.workspace?.group_id;
  $('workspace-existing').hidden = !groups.length || (active && groups.length === 1);
  $('workspace-reconnect-tabs').replaceChildren(
    ...(group?.tabs || []).map((tab) => {
      const item = document.createElement('li');
      item.textContent = tab.title;
      return item;
    }),
  );
  $('workspace-reconnect-hint').textContent = active
    ? 'This workspace is already connected. You can continue in the app.'
    : 'Reconnect these website tabs in the existing group. No tabs will be moved or recreated.';
  $('workspace-reconnect').textContent = active ? 'Workspace connected' : 'Reconnect workspace';
  $('workspace-reconnect').disabled =
    busy || active || !group || !latest?.connected || !latest?.workspaceSupported;
  $('workspace-group').disabled = busy;
}

function controls() {
  $('attach').disabled = busy || !$('tabs').value || Number($('tabs').value) === attachedTab;
  $('detach').disabled = busy || attachedTab == null;
  for (const id of ['tabs', 'reconnect', 'demo', 'pair', 'pair-code']) $(id).disabled = busy;
  $('forget').disabled = busy || !latest?.paired;
  $('single-panel').hidden = $('mode').value === 'workspace';
  $('workspace-panel').hidden = $('mode').value !== 'workspace';
  $('mode').disabled = busy;
  for (const id of [
    'workspace-tabs',
    'workspace-current',
    'allow-new',
    'workspace-save',
    'workspace-switch',
    'workspace-remove',
    'workspace-detach',
  ])
    $(id).disabled = busy || !latest?.connected || !latest?.workspaceSupported;
  if ($('mode').value === 'workspace') renderWorkspace();
}

function renderWorkspace() {
  const s = latest;
  if (!s) return;
  const w = s.workspace;
  renderExisting();
  const changed = workspaceId !== (w?.id ?? '');
  const signature = JSON.stringify([s.tabs, w]);
  if (signature !== workspaceSignature) {
    const picked =
      changed || !workspaceDirty
        ? w?.tabs.map((t) => String(t.id)) || []
        : Array.from($('workspace-tabs').selectedOptions, (o) => o.value);
    const windows = [...new Set(s.tabs.map((t) => t.windowId))];
    $('workspace-tabs').replaceChildren(
      ...s.tabs.map((t) => {
        const option = new Option(
          `Window ${windows.indexOf(t.windowId) + 1} · ${t.title}`,
          String(t.id),
        );
        option.selected = picked.includes(String(t.id));
        option.disabled = t.groupId >= 0 && t.groupId !== w?.group_id;
        return option;
      }),
    );
    const current = $('workspace-current').value;
    $('workspace-current').replaceChildren(
      ...(w?.tabs || []).map(
        (t) =>
          new Option(
            `${t.id === w.selected_tab ? '● ' : ''}${t.title || new URL(t.url).host}`,
            String(t.id),
          ),
      ),
    );
    $('workspace-current').value = w?.tabs.some((t) => String(t.id) === current)
      ? current
      : String(w?.selected_tab ?? '');
    workspaceSignature = signature;
  }
  if (changed) {
    $('allow-new').checked = w?.allow_new_tabs === true;
    $('workspace-setup').open = !w && !s.workspaces?.length;
    workspaceDirty = false;
  }
  workspaceId = w?.id ?? '';
  $('workspace-active').hidden = !w;
  $('workspace-setup-label').textContent = w
    ? 'Manage approved tabs'
    : s.workspaces?.length
      ? 'Create a different workspace'
      : 'Choose approved tabs';
  $('workspace-save').textContent = w ? 'Update approved tabs' : 'Create workspace';
  $('workspace-summary').textContent = !s.workspaceSupported
    ? 'Connect to the updated JevRev app to use tab workspaces.'
    : w
      ? `Workspace connected · ${w.tabs.length} approved tab${w.tabs.length === 1 ? '' : 's'} · New tabs ${w.allow_new_tabs ? 'allowed' : 'disabled'}`
      : s.workspaces?.length
        ? 'Your existing workspace is available above. Reconnect it to continue.'
        : 'Only tabs you select are controlled. Chrome will request permission to manage tab groups.';
}

async function refresh() {
  try {
    const s = await ask({ kind: 'state' });
    latest = s;
    if (!initialized)
      $('mode').value =
        s.workspace || (s.tab == null && s.workspaces?.length) ? 'workspace' : 'single';
    $('dot').className = 'dot' + (s.connected ? ' on' : '');
    $('status').textContent = s.connected
      ? 'Connected to PANINI'
      : !s.paired
        ? 'Not paired with PANINI yet'
        : s.lastError || 'Connecting to PANINI…';
    $('pairing').hidden = s.paired;
    if (!initialized) $('url').value = s.url;
    attachedTab = s.tab;
    const signature = JSON.stringify(s.tabs);
    if (signature !== tabSignature) {
      const selected = initialized
        ? $('tabs').value
        : String(s.currentSupported ? s.currentTab : (s.tab ?? ''));
      const placeholder = new Option(
        s.tabs.length ? 'Choose a website tab…' : 'No website tabs open',
        '',
      );
      $('tabs').replaceChildren(
        placeholder,
        ...s.tabs.map((t) => new Option(`${t.title} — ${new URL(t.url).host}`, String(t.id))),
      );
      $('tabs').value = s.tabs.some((t) => String(t.id) === selected) ? selected : '';
      tabSignature = signature;
    }
    $('hint').textContent = s.currentSupported
      ? 'Choose a tab from any Chrome window. Only the attached tab receives actions.'
      : 'New Tab and Chrome settings pages cannot be attached. Choose an open website below, or open the demo.';
    $('tab').textContent =
      s.tab != null
        ? `Attached: ${s.tabTitle || 'tab ' + s.tab}\n${s.tabUrl}` +
          (s.fullControl
            ? '\nFull control on: while PANINI works here, Chrome shows a "debugging this browser" bar. Press Cancel on it to stop PANINI at once.'
            : '')
        : 'No tab attached yet.';
    $('attach').textContent =
      attachedTab == null ? 'Attach selected tab' : 'Switch to selected tab';
    initialized = true;
    controls();
  } catch (error) {
    $('status').textContent = error.message;
  }
}

async function perform(action) {
  if (busy) return;
  busy = true;
  $('error').textContent = '';
  controls();
  try {
    await action();
  } catch (error) {
    // Polling the connection must never erase an attachment or permission error.
    $('error').textContent = error.message;
  } finally {
    busy = false;
    await refresh();
    controls();
  }
}

$('tabs').onchange = controls;
$('mode').onchange = controls;
$('workspace-group').onchange = renderExisting;
$('workspace-reconnect').onclick = () => {
  const group = existingGroup();
  // Capture exactly the tabs shown at click time, before asynchronous refreshes.
  const tabIds = group?.tabs.map((tab) => tab.id);
  return perform(() => {
    if (!group) throw new Error('Choose an existing JevRev workspace.');
    return ask({ kind: 'workspaceReconnect', groupId: group.group_id, tabIds });
  });
};
$('workspace-tabs').onchange = () => {
  workspaceDirty = true;
};
$('allow-new').onchange = () => {
  workspaceDirty = true;
};
$('workspace-save').onclick = () =>
  perform(async () => {
    const tabIds = Array.from($('workspace-tabs').selectedOptions, (o) => Number(o.value));
    if (!tabIds.length) throw new Error('Select at least one website tab.');
    if (!(await chrome.permissions.request({ permissions: ['tabGroups'] })))
      throw new Error('Tab-group permission was not granted. Single-tab mode is still available.');
    await ask({ kind: 'workspaceCreate', tabIds, allowNewTabs: $('allow-new').checked });
  });
$('workspace-switch').onclick = () =>
  perform(() => ask({ kind: 'workspaceSelect', tabId: Number($('workspace-current').value) }));
$('workspace-remove').onclick = () =>
  perform(() => ask({ kind: 'workspaceRemove', tabId: Number($('workspace-current').value) }));
$('workspace-detach').onclick = () => perform(() => ask({ kind: 'workspaceDetach' }));
$('attach').onclick = () =>
  perform(() => ask({ kind: 'attach', on: true, tabId: Number($('tabs').value) }));
$('detach').onclick = () => perform(() => ask({ kind: 'attach', on: false }));
$('reconnect').onclick = () => perform(() => ask({ kind: 'setUrl', url: $('url').value.trim() }));
$('demo').onclick = () => perform(() => ask({ kind: 'openDemo' }));
$('pair').onclick = () =>
  perform(async () => {
    await ask({ kind: 'pair', code: $('pair-code').value.trim() });
    $('pair-code').value = '';
  });
$('pair-code').onkeydown = (event) => {
  if (event.key === 'Enter') $('pair').click();
};
$('forget').onclick = () => perform(() => ask({ kind: 'forget' }));

$('version').textContent = `Claw ${chrome.runtime.getManifest?.()?.version || ''} · `;
refresh();
setInterval(refresh, 1500);
