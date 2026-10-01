// PANINI phone companion. Data is rendered with textContent only.
const $ = (id) => document.getElementById(id);

async function api(path, body) {
  const response = await fetch(path, {
    method: body ? 'POST' : 'GET',
    credentials: 'same-origin',
    headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(typeof data.detail === 'string' ? data.detail : `Error ${response.status}`);
    error.status = response.status;
    throw error;
  }
  return data;
}

function say(text, bad = false) {
  $('message').textContent = text;
  $('message').className = bad ? 'bad' : '';
}

function el(tag, cls, ...children) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  for (const child of children) if (child != null) node.append(child instanceof Node ? child : String(child));
  return node;
}

function button(label, onClick, cls = '') {
  const b = el('button', cls, label);
  b.type = 'button';
  b.addEventListener('click', async () => {
    b.disabled = true;
    try {
      await onClick();
      await load();
    } catch (error) {
      say(error.message, true);
    } finally {
      b.disabled = false;
    }
  });
  return b;
}

function fill(list, items, empty) {
  list.replaceChildren(...items);
  if (!items.length) list.append(el('li', 'empty', empty));
}

async function load() {
  let data;
  try {
    data = await api('/api/inbox');
  } catch (error) {
    if (error.status === 401) {
      $('pair').hidden = false;
      $('app').hidden = true;
      $('state').textContent = 'Not paired';
      return;
    }
    throw error;
  }
  $('pair').hidden = true;
  $('app').hidden = false;
  const s = data.status;
  $('state').textContent = s.running ? 'Working' : 'Idle';
  $('state').className = `pill ${s.running ? 'busy' : ''}`;
  fill(
    $('reviews'),
    data.reviews.map((r) => {
      const answer = el('input');
      answer.placeholder = r.kind === 'approval' ? 'Optional note' : 'Your answer';
      return el(
        'li',
        'card attention',
        el('strong', '', r.kind === 'approval' ? 'Approve this request?' : 'PANINI needs you'),
        el('p', '', r.message),
        answer,
        el(
          'div',
          'row',
          button(r.kind === 'approval' ? 'Approve' : 'Run again', () =>
            api(`/api/reviews/${r.id}`, { decision: 'rerun', answer: answer.value }),
          ),
          button('Decline', () => api(`/api/reviews/${r.id}`, { decision: 'dismiss' }), 'quiet'),
        ),
      );
    }),
    'Nothing is waiting for you.',
  );
  fill(
    $('notices'),
    data.notices.map((n) =>
      el(
        'li',
        'card',
        el('strong', '', n.title),
        el('p', '', n.body),
        button('Dismiss', () => api(`/api/notices/${n.id}`, {}), 'quiet'),
      ),
    ),
    'No news yet.',
  );
  fill(
    $('tasks'),
    data.tasks.map((t) =>
      el(
        'li',
        'card',
        el('span', 'pill', t.kind === 'watch' ? `watch · ${t.status}` : t.status),
        el('strong', '', t.title),
        t.result ? el('p', '', t.result.message) : null,
      ),
    ),
    'No tasks yet.',
  );
}

$('pair-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  try {
    await api('/api/pair', { code: form.get('code'), name: form.get('name') });
    say('Paired. This phone can now reach PANINI on your home network.');
    await load();
  } catch (error) {
    say(error.message, true);
  }
});

$('ask-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const target = event.currentTarget;
  try {
    await api('/api/ask', { text: new FormData(target).get('text') });
    target.reset();
    say('Sent. PANINI is on it; the result will appear in the inbox.');
    await load();
  } catch (error) {
    say(error.message, true);
  }
});

load().catch((error) => say(error.message, true));
setInterval(() => {
  if (!document.hidden) load().catch(() => {});
}, 8000);
