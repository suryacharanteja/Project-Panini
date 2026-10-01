let ws, pending;
let busy = false;
const tasks = new Map();
const $ = (id) => document.getElementById(id);
const escapeHtml = (value) =>
  String(value ?? '').replace(
    /[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' })[c],
  );
const money = (value) => `$${Number(value || 0).toFixed(6)}`;

function setBusy(value) {
  busy = value;
  $('runBtn').disabled = value;
  $('bulkBtn').disabled = value;
}

async function loadTasks() {
  try {
    const res = await fetch('/api/tasks');
    if (!res.ok) throw new Error('Task service unavailable. Refresh after startup completes.');
    const data = await res.json();
    if (!data.tasks?.length) throw new Error('No tasks available');
    $('task').replaceChildren();
    for (const task of data.tasks) {
      tasks.set(task.id, task);
      $('task').add(new Option(task.name, task.id));
    }
    onTaskChanged();
    $('text').value = data.tasks[0].examples[0] || '';
    setBusy(false);
  } catch (error) {
    showError(error.message);
  }
}

function onTaskChanged() {
  const task = tasks.get($('task').value);
  if (!task) return;
  $('task-description').textContent = task.description;
  $('task-description').classList.remove('hidden');
  $('task-meta').textContent = task.category;
  $('examples-grid').replaceChildren();
  task.examples.forEach((text, index) => {
    const button = document.createElement('button');
    button.className = 'example-btn';
    button.textContent = `Example ${index + 1}: ${text.slice(0, 42)}…`;
    button.title = text;
    button.onclick = () => {
      $('text').value = text;
      $('text').focus();
    };
    $('examples-grid').append(button);
  });
  $('examples-container').classList.toggle('hidden', !task.examples.length);
}

function connectWebSocket() {
  ws = new WebSocket(
    `${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/ws/compare`,
  );
  ws.onmessage = ({ data }) => {
    try {
      const result = JSON.parse(data);
      if (pending && result.id === pending.id) {
        clearTimeout(pending.timer);
        const { resolve, reject } = pending;
        pending = null;
        result.error ? reject(new Error(result.error)) : resolve(result);
      }
    } catch {
      if (pending) {
        clearTimeout(pending.timer);
        pending.reject(new Error('Invalid response'));
        pending = null;
      }
    }
  };
  ws.onclose = () => {
    if (pending) {
      clearTimeout(pending.timer);
      pending.reject(new Error('Connection lost. Please retry.'));
      pending = null;
    }
    setTimeout(connectWebSocket, 2000);
  };
  ws.onerror = () => ws.close();
}

async function compare(text, task) {
  if (ws?.readyState === WebSocket.OPEN)
    return new Promise((resolve, reject) => {
      const id = crypto.randomUUID();
      const timer = setTimeout(() => {
        pending = null;
        reject(new Error('Comparison timed out'));
      }, 70000);
      pending = { id, resolve, reject, timer };
      ws.send(JSON.stringify({ id, text, task }));
    });
  const res = await fetch('/api/compare', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text, task }),
    signal: AbortSignal.timeout(70000),
  });
  const result = await res.json();
  if (!res.ok)
    throw new Error(
      typeof result.detail === 'string'
        ? result.detail
        : result.error || `Request failed (${res.status})`,
    );
  return result;
}

async function runExamples(examples, task) {
  if (busy) return;
  if (!examples.length || !examples[0].trim()) return showError('Enter text to evaluate.');
  setBusy(true);
  $('results').classList.add('hidden');
  $('comparison-strip').classList.add('hidden');
  $('loading').classList.remove('hidden');
  $('loading').innerHTML =
    '<div class="spinner"></div><p>Running Jev and Direct LLM in parallel…</p>';
  const results = [];
  try {
    for (const text of examples) {
      results.push(await compare(text, task));
      $('loading').textContent = `Finished ${results.length}/${examples.length} examples…`;
    }
    const last = results.at(-1);
    $('direct-result').innerHTML = card(last.direct);
    $('jev-result').innerHTML = card(last.jev, true);
    $('loading').classList.add('hidden');
    $('results').classList.remove('hidden');
    displayMetrics(results);
  } catch (error) {
    showError(error.message);
  } finally {
    setBusy(false);
  }
}

function runDemo() {
  return runExamples([$('text').value.trim()], $('task').value);
}
function runBulkDemo() {
  return runExamples(tasks.get($('task').value)?.examples.slice(0, 3) || [], $('task').value);
}

function bars(answers) {
  return Object.entries(answers || {})
    .sort((a, b) => b[1].probability - a[1].probability)
    .map(([key, answer]) => {
      const p = Math.max(0, Math.min(100, Number(answer.probability || 0) * 100));
      return `<div style="margin:6px 0"><div style="display:flex;justify-content:space-between;font-size:.8em"><span>${escapeHtml(key)}</span><span>${p.toFixed(1)}%</span></div><div class="confidence-bar"><div class="confidence-fill" style="width:${p}%"></div></div></div>`;
    })
    .join('');
}

function card(result, jev = false) {
  if (!result) return '<div class="error">No result returned</div>';
  if (result.error) return `<div class="error">${escapeHtml(result.error)}</div>`;
  const item = (label, value) =>
    `<div class="result-item"><div class="label">${label}</div><div class="value">${value}</div></div>`;
  return (
    item('Highest probability', escapeHtml(result.result)) +
    item(jev ? 'Jev probabilities' : 'LLM self-reported probabilities', bars(result.answers)) +
    item('Latency', `${result.latency_ms}ms`) +
    item('Tokens', Number(result.tokens) || 0) +
    item(
      result.cost_estimated ? 'Estimated cost (token usage)' : 'Reported cost',
      money(result.cost),
    ) +
    (result.reasoning ? item('Reasoning', escapeHtml(result.reasoning)) : '') +
    (jev
      ? item('Generation ID', escapeHtml(result.generation_id || 'Not supplied by provider'))
      : '') +
    `<details class="expandable"><summary>Full response</summary><pre class="code">${escapeHtml(JSON.stringify(jev ? result.full_response : result, null, 2))}</pre></details>`
  );
}

function displayMetrics(results) {
  const complete = results.filter((r) => r.direct && r.jev && !r.direct.error && !r.jev.error);
  const strip = $('comparison-strip');
  strip.classList.remove('hidden');
  if (!complete.length) {
    strip.textContent = 'No successful pairs to compare. Provider failures are shown above.';
    return;
  }
  const mean = (provider, metric) =>
    complete.reduce((sum, r) => sum + Number(r[provider][metric] || 0), 0) / complete.length;
  const directMs = mean('direct', 'latency_ms'),
    jevMs = mean('jev', 'latency_ms'),
    directCost = mean('direct', 'cost'),
    jevCost = mean('jev', 'cost');
  const ratio = jevMs > 0 ? (directMs / jevMs).toFixed(2) : '—';
  const saving =
    directCost > 0 ? `${((1 - jevCost / directCost) * 100).toFixed(1)}%` : 'Unavailable';
  strip.innerHTML = `<div class="metric"><div class="num">${ratio}×</div><div class="cap">Direct / Jev latency</div></div><div class="metric"><div class="num">${saving}</div><div class="cap">estimated cost reduction</div></div><div class="explain">${complete.length}/${results.length} successful pairs. Mean latency: Direct ${Math.round(directMs)}ms, Jev ${Math.round(jevMs)}ms. Mean cost: Direct ${money(directCost)}, Jev ${money(jevCost)}. Failed pairs are excluded. Each question is independent; the top score is not a complete routing policy.</div>`;
}

function showError(message) {
  $('loading').classList.remove('hidden');
  $('loading').innerHTML = `<div class="error">${escapeHtml(message)}</div>`;
  $('comparison-strip').classList.add('hidden');
}
document.addEventListener('DOMContentLoaded', () => {
  setBusy(true);
  loadTasks();
  connectWebSocket();
  $('text').addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) runDemo();
  });
});
