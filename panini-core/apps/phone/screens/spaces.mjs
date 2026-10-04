// The owner's notebook on the phone. Content stays text, revisions use CAS, drafts stay local.
import { action, h, toast } from './ui.mjs';

export async function request(fabric, method, path, body) {
  const result = await fabric.request(method, `/spaces${path}`, body);
  if (result.status !== 200) {
    const detail = result.body?.detail;
    throw new Error(detail?.message || detail || 'Notebook request failed.');
  }
  return result.body;
}

export function saveSource(app, kind, sourceId) {
  const panel = h('div');
  return h(
    'div',
    {},
    h(
      'button',
      {
        onclick: (event) =>
          action(event.currentTarget, async () => {
            const { spaces } = await request(app.fabric, 'GET', '');
            const select = h(
              'select',
              { 'aria-label': 'Save destination' },
              spaces.map((space) => h('option', { value: space.id, text: space.name })),
            );
            panel.replaceChildren(
              spaces.length
                ? h(
                    'div',
                    {},
                    select,
                    h(
                      'button',
                      {
                        onclick: (event) =>
                          action(event.currentTarget, async () => {
                            const page = await request(app.fabric, 'POST', '/save-source', {
                              space_id: select.value,
                              kind,
                              source_id: sourceId,
                            });
                            toast(
                              page.deleted_at
                                ? 'Already saved in Trash. Restore it in your notebook.'
                                : 'Saved to your notebook.',
                            );
                            panel.replaceChildren();
                          }),
                      },
                      'Save source',
                    ),
                  )
                : h('p', { text: 'Create a space in your notebook first.' }),
            );
          }),
      },
      'Save to Space',
    ),
    panel,
  );
}

export async function spaces(app) {
  const root = h('section.screen', {}, h('h2', { text: 'Notebook' }));
  let available;
  try {
    available = (await request(app.fabric, 'GET', '')).spaces;
  } catch (error) {
    root.append(h('p.muted', { text: error.message }));
    return root;
  }
  const select = h(
    'select',
    { 'aria-label': 'Notebook space' },
    h('option', { value: '', text: 'Choose a space' }),
    available.map((space) => h('option', { value: space.id, text: space.name })),
  );
  const list = h('div'),
    editor = h('div');
  let deleted = false,
    generation = 0;
  async function library() {
    const revision = ++generation;
    editor.replaceChildren();
    if (!select.value) {
      list.replaceChildren();
      return;
    }
    const { pages } = await request(app.fabric, 'GET', `/${select.value}/pages?deleted=${deleted}`);
    if (revision !== generation) return;
    list.replaceChildren(
      ...pages.map((page) =>
        h(
          'div.card',
          {},
          h(
            'button',
            {
              onclick: (event) => action(event.currentTarget, () => open(page.id)),
              disabled: deleted,
            },
            page.title,
          ),
          h(
            'button',
            {
              onclick: (event) =>
                action(event.currentTarget, async () => {
                  if (!deleted && !confirm(`Move ${page.title} to Trash?`)) return;
                  const current = await request(
                    app.fabric,
                    'GET',
                    `/pages/${page.id}?deleted=true`,
                  );
                  await request(app.fabric, 'PATCH', `/pages/${page.id}`, {
                    expected_revision: current.revision,
                    [deleted ? 'restore' : 'trash']: true,
                  });
                  await library();
                }),
            },
            deleted ? 'Restore page' : 'Trash page',
          ),
        ),
      ),
    );
  }
  async function open(id) {
    const revision = ++generation;
    const page = await request(app.fabric, 'GET', `/pages/${id}`);
    if (revision !== generation) return;
    const key = `panini.phone.page.${id}`;
    let draft;
    try {
      draft = JSON.parse(localStorage.getItem(key) || 'null');
    } catch {
      draft = null;
    }
    let expected = Number.isInteger(draft?.revision) ? draft.revision : page.revision;
    const title = h('input', { 'aria-label': 'Notebook page title', maxlength: 240 });
    const content = h('textarea', {
      'aria-label': 'Notebook Markdown',
      maxlength: 200000,
      rows: 16,
    });
    title.value = typeof draft?.title === 'string' ? draft.title : page.title;
    content.value = typeof draft?.content === 'string' ? draft.content : page.content;
    const status = h('p', {
      role: 'status',
      text: draft ? 'Recovered local draft.' : `Revision ${page.revision}`,
    });
    const persist = () => {
      try {
        localStorage.setItem(
          key,
          JSON.stringify({ title: title.value, content: content.value, revision: expected }),
        );
      } catch {
        status.textContent = 'Draft storage unavailable. Keep this page open and copy your text.';
      }
    };
    title.addEventListener('input', persist);
    content.addEventListener('input', persist);
    editor.replaceChildren(
      h(
        'article.card',
        {},
        title,
        content,
        status,
        h(
          'button.primary',
          {
            onclick: (event) =>
              action(event.currentTarget, async () => {
                const snapshot = { title: title.value, content: content.value, revision: expected };
                const savedDraft = JSON.stringify(snapshot);
                persist();
                try {
                  const saved = await request(app.fabric, 'PATCH', `/pages/${id}`, {
                    expected_revision: expected,
                    title: snapshot.title,
                    content: snapshot.content,
                  });
                  expected = saved.revision;
                  if (localStorage.getItem(key) === savedDraft) localStorage.removeItem(key);
                  else if (revision === generation) persist();
                  status.textContent = `Saved revision ${saved.revision}.`;
                  editor.querySelector('[data-conflict]')?.remove();
                } catch (error) {
                  status.textContent = `${error.message} Your draft is kept. Reopen the page to compare.`;
                  throw error;
                }
              }),
          },
          'Save page',
        ),
        ...['markdown', 'pdf'].map((format) =>
          h(
            'button',
            {
              onclick: (event) =>
                action(event.currentTarget, async () => {
                  const result = await request(app.fabric, 'GET', `/pages/${id}/${format}`);
                  const bytes = result.base64
                    ? Uint8Array.from(atob(result.base64), (c) => c.charCodeAt(0))
                    : result.text;
                  const url = URL.createObjectURL(
                    new Blob([bytes], {
                      type: format === 'pdf' ? 'application/pdf' : 'text/markdown',
                    }),
                  );
                  const link = h('a', {
                    href: url,
                    download: `panini-${id}.${format === 'pdf' ? 'pdf' : 'md'}`,
                  });
                  document.body.append(link);
                  link.click();
                  link.remove();
                  setTimeout(() => URL.revokeObjectURL(url), 60000);
                }),
            },
            format === 'pdf' ? 'Export PDF' : 'Export Markdown',
          ),
        ),
        draft && draft.revision !== page.revision
          ? h(
              'details',
              { 'data-conflict': true },
              h('summary', { text: 'Compare server revision' }),
              h('pre', { text: page.content }),
              h(
                'button',
                {
                  onclick: () => {
                    if (!confirm(`Replace revision ${page.revision} with this local draft?`))
                      return;
                    expected = page.revision;
                    persist();
                    status.textContent = 'Reviewed conflict. Press Save page to commit.';
                  },
                },
                'Use current revision',
              ),
            )
          : null,
      ),
    );
  }
  select.addEventListener('change', () => library().catch((error) => toast(error.message, 'warn')));
  root.append(
    select,
    h(
      'button',
      {
        onclick: (event) =>
          action(event.currentTarget, async () => {
            const name = prompt('Name your space');
            if (!name) return;
            await request(app.fabric, 'POST', '', { name });
            await app.show('spaces');
          }),
      },
      'New space',
    ),
    h(
      'button',
      {
        onclick: (event) =>
          action(event.currentTarget, async () => {
            if (!select.value) throw new Error('Choose a space first.');
            const title = prompt('Name your page');
            if (!title) return;
            const page = await request(app.fabric, 'POST', '/pages', {
              space_id: select.value,
              title,
            });
            await library();
            await open(page.id);
          }),
      },
      'New page',
    ),
    h(
      'button',
      {
        onclick: (event) =>
          action(event.currentTarget, async () => {
            deleted = !deleted;
            event.currentTarget.textContent = deleted ? 'Active pages' : 'Trash';
            await library();
          }),
      },
      'Trash',
    ),
    list,
    editor,
  );
  return root;
}
