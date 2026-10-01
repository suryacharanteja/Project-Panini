const $ = (id) => document.getElementById(id);
const controller = new AbortController();
let cursor = 0,
  sessionId = '',
  instance = '';
const storage = 'panini.activity';
async function request(path, body) {
  const response = await fetch(`/api/panini${path}`, {
    method: body ? 'POST' : 'GET',
    headers: { 'Content-Type': 'application/json', 'X-Panini-Instance': instance },
    body: body ? JSON.stringify(body) : undefined,
    signal: controller.signal,
  });
  if (!response.ok)
    throw new Error(
      response.status === 401
        ? 'Viewer access expired. Open a new dashboard from PANINI.'
        : 'The activity connection is unavailable.',
    );
  return response.json();
}
function results(items) {
  $('results').replaceChildren(
    ...items.map((item) => {
      const box = document.createElement('div');
      box.className = 'result';
      const title = document.createElement(item.url ? 'a' : 'strong');
      title.textContent = item.title;
      if (item.url) {
        try {
          const url = new URL(item.url);
          if (['http:', 'https:'].includes(url.protocol) && !url.username && !url.password) {
            title.href = url.href;
            title.target = '_blank';
            title.rel = 'noopener noreferrer';
          }
        } catch {}
      }
      const detail = document.createElement('p');
      detail.textContent = item.detail;
      box.append(title, detail);
      return box;
    }),
  );
}
async function snapshot() {
  const value = await request(`/sessions/${sessionId}`);
  cursor = value.last_event_seq;
  $('state').textContent = value.task?.state || value.state;
  $('capability').textContent = value.task?.capability || '—';
  $('target').textContent = value.task?.target_label || '—';
  results(value.results || []);
  return value;
}
function event(value) {
  if (!value.sequence || value.sequence <= cursor) return;
  cursor = value.sequence;
  const data = value.data || {};
  if (value.type.startsWith('caption.')) {
    if (!data.redacted)
      $('captions').textContent =
        ($('captions').dataset.active ? $('captions').textContent : '') +
        (data.delta || data.text || '');
    $('captions').dataset.active = 'true';
    $('captions').textContent = $('captions').textContent.slice(-4000);
    return;
  }
  const row = document.createElement('li'),
    time = document.createElement('small'),
    content = document.createElement('span');
  time.textContent = `${new Date(value.timestamp).toLocaleTimeString()} · ${value.type}`;
  content.textContent = data.message || data.label || data.state || value.type.replaceAll('.', ' ');
  row.append(time, content);
  $('events').prepend(row);
  while ($('events').children.length > 200) $('events').lastChild.remove();
  if (value.type.startsWith('task.')) $('state').textContent = value.type.slice(5);
  if (value.capability) $('capability').textContent = value.capability;
  if (value.target_label) $('target').textContent = value.target_label;
  if (data.results) results(data.results);
}
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
/** The signed-in web console may watch the live PANINI conversation (read-only). */
async function consoleTicket() {
  const response = await fetch('/api/console/panini-observer', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: '{}',
    signal: controller.signal,
  });
  if (!response.ok)
    throw new Error(
      response.status === 401
        ? 'Sign in to the PANINI console to watch activity.'
        : 'The activity connection is unavailable.',
    );
  return (await response.json()).ticket;
}
async function attach(ticket) {
  const credentials = await request('/observer/exchange', { ticket });
  sessionId = credentials.session_id;
  instance = credentials.instance_id;
  cursor = 0;
  sessionStorage.setItem(storage, JSON.stringify({ sessionId, instance }));
}
async function follow() {
  await snapshot();
  $('connection').textContent =
    'Connected to PANINI. This view receives verified events and results.';
  while (!controller.signal.aborted) {
    const response = await fetch(`/api/panini/sessions/${sessionId}/events?after=${cursor}`, {
      headers: { 'X-Panini-Instance': instance },
      signal: controller.signal,
    });
    if (response.status === 409) {
      await snapshot();
      continue;
    }
    if (!response.ok) return; // The conversation ended or access expired.
    const reader = response.body.getReader(),
      decoder = new TextDecoder();
    let pending = '';
    try {
      while (true) {
        const chunk = await reader.read();
        if (chunk.done) break;
        pending += decoder.decode(chunk.value, { stream: true });
        if (pending.length > 1048576) throw new Error('Activity frame exceeded limit.');
        let index;
        while ((index = pending.indexOf('\n')) >= 0) {
          const line = pending.slice(0, index);
          pending = pending.slice(index + 1);
          if (line) event(JSON.parse(line));
        }
      }
    } finally {
      await reader.cancel().catch(() => {});
    }
    await sleep(1500);
  }
}
async function connect() {
  let ticket = decodeURIComponent(location.hash.slice(1));
  history.replaceState(null, '', '/activity');
  const saved = JSON.parse(sessionStorage.getItem(storage) || '{}');
  if (!ticket && saved.sessionId) ({ sessionId, instance } = saved);
  while (!controller.signal.aborted) {
    try {
      if (!sessionId) {
        ticket ||= await consoleTicket();
        if (!ticket) {
          $('connection').textContent =
            'PANINI has no active conversation. This view connects when one starts.';
          await sleep(5000);
          continue;
        }
        await attach(ticket);
        ticket = '';
      }
      await follow();
      $('connection').textContent = 'The conversation ended. Waiting for the next one…';
    } catch (error) {
      $('connection').textContent = error.message;
      await sleep(3000);
    }
    sessionId = '';
    sessionStorage.removeItem(storage);
  }
}
window.addEventListener('pagehide', () => controller.abort());
connect().catch((error) => {
  $('connection').textContent = error.message;
});
