// PANINI console: memories, tasks, watches, reviews, notices, skills and connectors.
// All data is rendered with textContent; nothing from the API is parsed as HTML.

const $ = (id) => document.getElementById(id);
const status = $('notice');

async function api(path, { method = 'GET', body } = {}) {
  const response = await fetch(`/api/harness${path}`, {
    method,
    credentials: 'same-origin',
    headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const message =
      data.error?.message ||
      (typeof data.detail === 'string' ? data.detail : data.detail?.[0]?.msg) ||
      `Request failed (${response.status}).`;
    throw new Error(response.status === 401 ? 'Sign in to the PANINI console first.' : message);
  }
  return data;
}

function say(message, isError = false) {
  status.textContent = message;
  status.classList.toggle('error', isError);
}

function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (key === 'class') node.className = value;
    else if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value);
  }
  for (const child of children.flat()) {
    if (child == null || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

const when = (seconds) =>
  seconds
    ? new Date(seconds * 1000).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })
    : '';

function act(label, handler, cls = 'quiet') {
  return el(
    'button',
    {
      class: cls,
      type: 'button',
      onclick: async (event) => {
        const button = event.currentTarget; // Null after the first await.
        button.disabled = true;
        try {
          await handler();
          await refresh();
        } catch (error) {
          say(error.message, true);
        } finally {
          button.disabled = false;
        }
      },
    },
    label,
  );
}

function empty(list, text) {
  if (!list.children.length) list.append(el('li', { class: 'empty' }, text));
}

async function loadStatus() {
  const s = await api('/status');
  $('s-running').textContent = s.running ? s.current?.title || 'Working' : 'Idle';
  $('s-queued').textContent = String(s.queued);
  $('s-reviews').textContent = String(s.reviews);
  $('s-memories').textContent = String(s.memories);
  const inbox = document.querySelector('[data-tab="inbox"]');
  inbox.textContent = s.notices || s.reviews ? `Inbox (${s.notices + s.reviews})` : 'Inbox';
}

async function loadInbox() {
  const [{ reviews }, { notices }] = await Promise.all([api('/reviews'), api('/notices')]);
  const reviewList = $('reviews');
  reviewList.replaceChildren(
    ...reviews.map((r) => {
      const answer = el('input', {
        placeholder: r.kind === 'question' ? 'Your answer' : 'Optional note',
        'aria-label': 'Answer',
        maxlength: '1000',
      });
      return el(
        'li',
        { class: 'card attention' },
        el(
          'strong',
          {},
          r.kind === 'outcome_unknown'
            ? 'Outcome unknown'
            : r.kind === 'approval'
              ? 'Another agent asks PANINI — approve?'
              : 'Question',
        ),
        el('p', {}, r.message),
        el('small', {}, `Expires ${when(r.expires)}`),
        el(
          'div',
          { class: 'row' },
          answer,
          act(r.kind === 'approval' ? 'Approve' : 'Run again', () =>
            api(`/reviews/${r.id}`, {
              method: 'POST',
              body: { decision: 'rerun', answer: answer.value },
            }),
          ),
          act('Dismiss', () =>
            api(`/reviews/${r.id}`, { method: 'POST', body: { decision: 'dismiss' } }),
          ),
        ),
      );
    }),
  );
  empty(reviewList, 'Nothing is waiting for you.');
  const noticeList = $('notices');
  noticeList.replaceChildren(
    ...notices.map((n) =>
      el(
        'li',
        { class: `card ${n.status === 'unread' ? 'unread' : ''}` },
        el('span', { class: `tag ${n.kind}` }, n.kind),
        el('strong', {}, n.title),
        el('p', {}, n.body),
        el('small', {}, when(n.created)),
        el(
          'div',
          { class: 'row' },
          n.action?.type === 'schedule' || n.action?.type === 'skill'
            ? act(
                n.action.type === 'schedule' ? 'Make it a routine' : 'Draft the skill',
                () => api(`/notices/${n.id}/accept`, { method: 'POST' }),
                '',
              )
            : null,
          n.action?.type === 'open_review'
            ? el('span', { class: 'hint' }, 'Decide in “Waiting for you” above.')
            : null,
          n.status === 'unread'
            ? act('Mark read', () =>
                api(`/notices/${n.id}`, { method: 'POST', body: { status: 'read' } }),
              )
            : null,
          act('Dismiss', () =>
            api(`/notices/${n.id}`, { method: 'POST', body: { status: 'dismissed' } }),
          ),
        ),
      ),
    ),
  );
  empty(noticeList, 'No updates yet.');
}

async function loadTasks() {
  const { tasks } = await api('/tasks');
  const list = $('tasks');
  list.replaceChildren(
    ...tasks.map((t) =>
      el(
        'li',
        { class: 'card' },
        el(
          'span',
          { class: `tag ${t.status}` },
          t.kind === 'watch' ? `watch · ${t.status}` : t.status,
        ),
        el('strong', {}, t.title),
        t.condition ? el('p', {}, `Tell me when: ${t.condition}`) : null,
        el(
          'small',
          {},
          [
            t.every_minutes ? `Every ${t.every_minutes} min` : 'Once',
            t.next_run && t.status === 'queued' ? `next ${when(t.next_run)}` : '',
          ]
            .filter(Boolean)
            .join(' · '),
        ),
        t.result ? el('p', { class: 'result' }, t.result.message) : null,
        ['queued', 'running', 'needs_review'].includes(t.status)
          ? el(
              'div',
              { class: 'row' },
              act('Cancel', () => api(`/tasks/${t.id}/cancel`, { method: 'POST' })),
            )
          : null,
      ),
    ),
  );
  empty(list, 'No tasks yet. Add one above or ask PANINI to do something later.');
}

async function loadMemories(query = '') {
  const { memories } = await api(`/memories${query ? `?q=${encodeURIComponent(query)}` : ''}`);
  const list = $('memories');
  list.replaceChildren(
    ...memories.map((m) => {
      const text = el('p', {}, m.text);
      return el(
        'li',
        { class: 'card' },
        el('span', { class: 'tag' }, m.kind),
        text,
        el('small', {}, `From ${m.source}${m.created ? ` · ${when(m.created)}` : ''}`),
        el(
          'div',
          { class: 'row' },
          act('Edit', async () => {
            const next = prompt('Edit memory', m.text);
            if (next && next.trim() !== m.text)
              await api(`/memories/${m.id}`, { method: 'PATCH', body: { text: next.trim() } });
          }),
          act('Forget', () => api(`/memories/${m.id}`, { method: 'DELETE' }), 'danger'),
        ),
      );
    }),
  );
  empty(list, query ? 'No matching memories.' : 'PANINI has not remembered anything yet.');
}

async function loadSkills() {
  const { skills } = await api('/skills');
  const list = $('skills');
  list.replaceChildren(
    ...skills.map((s) => {
      const body = el('pre', { hidden: '' });
      return el(
        'li',
        { class: 'card' },
        el('span', { class: 'tag' }, s.origin),
        el('strong', {}, s.name),
        el('p', {}, s.description),
        el(
          'div',
          { class: 'row' },
          el(
            'button',
            {
              class: 'quiet',
              type: 'button',
              // Local toggle: no list refresh, which would discard the opened steps.
              onclick: async () => {
                try {
                  if (!body.textContent)
                    body.textContent = (await api(`/skills/${s.name}`)).instructions;
                  body.hidden = !body.hidden;
                } catch (error) {
                  say(error.message, true);
                }
              },
            },
            'Show steps',
          ),
        ),
        body,
      );
    }),
  );
  empty(list, 'No skills installed.');
}

async function loadConnectors(reload = false) {
  const data = reload
    ? await api('/connectors/reload', { method: 'POST' })
    : await api('/connectors');
  $('connectors-path').textContent = data.path;
  const list = $('connectors');
  list.replaceChildren(
    ...data.connectors.map((c) =>
      el(
        'li',
        { class: 'card' },
        el('span', { class: `tag ${c.state}` }, c.state),
        el('strong', {}, c.name),
        c.error ? el('p', { class: 'error' }, c.error) : null,
        c.kind === 'remote'
          ? el(
              'div',
              { class: 'row' },
              c.state === 'sign_in' || !c.signed_in
                ? act(
                    'Sign in',
                    async () => {
                      const { url, signed_in: already } = await api(
                        `/connectors/${encodeURIComponent(c.name)}/sign-in`,
                        { method: 'POST' },
                      );
                      if (already) return say(`${c.name} is already connected.`);
                      // The provider's page opens in a new tab and returns to PANINI afterwards.
                      window.open(url, '_blank', 'noopener');
                      say(`Finish signing in to ${c.name} in the new tab, then press Reload.`);
                    },
                    '',
                  )
                : act('Sign out', () =>
                    api(`/connectors/${encodeURIComponent(c.name)}/sign-out`, { method: 'POST' }),
                  ),
            )
          : null,
        el(
          'ul',
          { class: 'tools' },
          c.tools.map((t) =>
            el(
              'li',
              {},
              el('code', {}, t.name),
              t.read_only ? ' (read-only) ' : ' ',
              t.description,
            ),
          ),
        ),
      ),
    ),
  );
  empty(list, 'No connectors configured.');
}

async function loadAgents() {
  const { clients, remotes } = await api('/a2a/clients');
  const list = $('a2a-clients');
  list.replaceChildren(
    ...clients.map((c) =>
      el(
        'li',
        { class: 'card' },
        el('strong', {}, c.name),
        el(
          'small',
          {},
          `Created ${when(c.created)}${c.last_used ? ` · last used ${when(c.last_used)}` : ''}`,
        ),
        el(
          'div',
          { class: 'row' },
          act('Revoke', () => api(`/a2a/clients/${c.id}`, { method: 'DELETE' }), 'danger'),
        ),
      ),
    ),
  );
  empty(list, 'No agent can reach PANINI yet.');
  const remoteList = $('a2a-remotes');
  remoteList.replaceChildren(
    ...remotes.map((name) => el('li', { class: 'card' }, el('strong', {}, name))),
  );
  empty(remoteList, 'None configured.');
}

async function loadPhone() {
  const s = await api('/mobile');
  $('phone-toggle').textContent = s.enabled ? 'Turn off' : 'Turn on';
  $('phone-toggle').className = s.enabled ? 'quiet' : '';
  $('phone-on').hidden = !s.enabled;
  $('phone-urls').textContent = s.urls.join('  or  ') || 'no home-network address found';
  if (s.error) say(s.error, true);
  const list = $('phone-devices');
  list.replaceChildren(
    ...s.devices.map((d) =>
      el(
        'li',
        { class: 'card' },
        el('strong', {}, d.name),
        el(
          'small',
          {},
          `Paired ${when(d.created)}${d.last_seen ? ` · seen ${when(d.last_seen)}` : ''}`,
        ),
        el(
          'div',
          { class: 'row' },
          act('Remove', () => api(`/mobile/devices/${d.id}`, { method: 'DELETE' }), 'danger'),
        ),
      ),
    ),
  );
  empty(list, 'No phone paired yet.');
}

const loaders = {
  inbox: loadInbox,
  tasks: loadTasks,
  memories: () => loadMemories(),
  skills: loadSkills,
  connectors: () => loadConnectors(),
  agents: loadAgents,
  phone: loadPhone,
};
let current = 'inbox';

async function refresh() {
  try {
    await Promise.all([loadStatus(), loaders[current]()]);
    if (status.classList.contains('error')) say('');
  } catch (error) {
    say(error.message, true);
  }
}

for (const tab of document.querySelectorAll('[role="tab"]')) {
  tab.addEventListener('click', () => {
    current = tab.dataset.tab;
    for (const other of document.querySelectorAll('[role="tab"]'))
      other.setAttribute('aria-selected', String(other === tab));
    for (const panel of document.querySelectorAll('[role="tabpanel"]'))
      panel.hidden = panel.id !== `tab-${current}`;
    void refresh();
  });
}

$('task-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const target = event.currentTarget;
  const form = new FormData(target);
  const every = Number(form.get('every')) || null;
  const condition = String(form.get('condition') || '').trim() || null;
  try {
    if (condition && !every) throw new Error('A watch needs “Repeat every”.');
    await api('/tasks', {
      method: 'POST',
      body: {
        goal: form.get('goal'),
        delay_minutes: Number(form.get('delay')) || 0,
        every_minutes: every,
        condition,
      },
    });
    target.reset();
    say(condition ? 'Watch added.' : 'Task added.');
    await refresh();
  } catch (error) {
    say(error.message, true);
  }
});

$('memory-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const target = event.currentTarget;
  const form = new FormData(target);
  try {
    await api('/memories', {
      method: 'POST',
      body: { text: form.get('text'), kind: form.get('kind') },
    });
    target.reset();
    say('Remembered.');
    await refresh();
  } catch (error) {
    say(error.message, true);
  }
});

$('memory-search').addEventListener('submit', (event) => {
  event.preventDefault();
  loadMemories(String(new FormData(event.currentTarget).get('q') || '').trim()).catch((error) =>
    say(error.message, true),
  );
});

$('forget-all').addEventListener('click', async () => {
  if (!confirm('Forget every memory PANINI has about you? This cannot be undone.')) return;
  try {
    const { deleted } = await api('/memories', { method: 'DELETE' });
    say(`Forgot ${deleted} memories.`);
    await refresh();
  } catch (error) {
    say(error.message, true);
  }
});

$('export').addEventListener('click', async () => {
  try {
    const data = await api('/memories/export');
    const link = el('a', {
      href: URL.createObjectURL(
        new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }),
      ),
      download: 'panini-memories.json',
    });
    link.click();
    URL.revokeObjectURL(link.href);
  } catch (error) {
    say(error.message, true);
  }
});

$('refresh-suggestions').addEventListener('click', async () => {
  try {
    const { created } = await api('/suggestions/refresh', { method: 'POST' });
    say(created.length ? `${created.length} new suggestion(s).` : 'No new suggestions right now.');
    await refresh();
  } catch (error) {
    say(error.message, true);
  }
});

$('reload-connectors').addEventListener('click', () =>
  loadConnectors(true).catch((error) => say(error.message, true)),
);

void refresh();
setInterval(() => {
  if (!document.hidden && current !== 'memories') void refresh();
}, 10000);

$('a2a-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const target = event.currentTarget;
  try {
    const client = await api('/a2a/clients', {
      method: 'POST',
      body: { name: new FormData(target).get('name') },
    });
    target.reset();
    const box = $('a2a-token');
    box.hidden = false;
    box.textContent = `Token for ${client.name} (shown once; give it to that agent as a Bearer token): ${client.token}`;
    await refresh();
  } catch (error) {
    say(error.message, true);
  }
});

$('phone-toggle').addEventListener('click', async (event) => {
  const button = event.currentTarget;
  button.disabled = true;
  try {
    const on = button.textContent === 'Turn on';
    await api(on ? '/mobile/enable' : '/mobile/disable', { method: 'POST' });
    $('phone-code-value').hidden = true;
    await refresh();
  } catch (error) {
    say(error.message, true);
  } finally {
    button.disabled = false;
  }
});

$('phone-code').addEventListener('click', async () => {
  try {
    const { code, expires } = await api('/mobile/code', { method: 'POST' });
    const box = $('phone-code-value');
    box.hidden = false;
    box.textContent = `Pairing code: ${code} (valid until ${new Date(expires * 1000).toLocaleTimeString()}, one use)`;
  } catch (error) {
    say(error.message, true);
  }
});
