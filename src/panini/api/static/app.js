/* Panini web UI.
 *
 * The approval dialog implements DD-134/135 literally: Deny holds focus, Enter
 * denies, and the highest-risk case (egress on a tainted turn to an unknown
 * destination) enforces a short read delay before Approve becomes clickable.
 * A reflexive keypress must always be the safe one.
 */

const $ = (id) => document.getElementById(id);
const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
};

const state = { busy: false, currentAssistant: null, pendingApproval: null };

/* ── tabs ──────────────────────────────────────────────────────────── */

document.querySelectorAll("nav button").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll("nav button").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
    btn.classList.add("active");
    $(`tab-${btn.dataset.tab}`).classList.add("active");
    if (btn.dataset.tab === "memory") loadMemory();
    if (btn.dataset.tab === "audit") loadAudit();
  });
});

/* ── event stream ──────────────────────────────────────────────────── */

let backoff = 500;

function connect() {
  const es = new EventSource("/events");

  es.onopen = () => {
    backoff = 500;
    $("conn").classList.remove("down");
    $("conn").title = "connected";
  };

  // Disconnection is made visible rather than silently degrading.
  es.onerror = () => {
    es.close();
    $("conn").classList.add("down");
    $("conn").title = "reconnecting…";
    setTimeout(connect, backoff);
    backoff = Math.min(backoff * 2, 10000);
  };

  const on = (type, fn) =>
    es.addEventListener(type, (e) => {
      try { fn(JSON.parse(e.data)); } catch { /* malformed frame — ignore */ }
    });

  on("provider.changed", (d) => {
    const p = $("provider");
    p.textContent = `${d.provider} · ${d.model} · ${d.is_local ? "local" : "cloud"}`;
    p.className = `provider ${d.is_local ? "local" : "cloud"}`;
    p.title = d.is_local
      ? "Running locally — nothing leaves this machine"
      : "Cloud model — your messages are sent to a third party";
  });

  on("segment.emitted", (d) => appendSegment(d));
  on("approval.requested", (d) => showApproval(d));
  on("approval.resolved", () => hideApproval());
  on("tool.invoked", (d) => appendToolNote(d));
  on("error", (d) => appendError(d));
  on("context.trimmed", (d) =>
    appendNote(`Earlier context trimmed (${d.dropped_turns} turns dropped).`));
}

/* ── chat ──────────────────────────────────────────────────────────── */

const messages = $("messages");

function scroll() { messages.scrollTop = messages.scrollHeight; }

function appendUser(text) {
  const m = el("div", "msg user");
  m.append(el("div", "who", "you"), el("div", "bubble", text));
  messages.append(m);
  scroll();
}

function startAssistant() {
  const m = el("div", "msg assistant");
  m.append(el("div", "who", "panini"));
  const body = el("div", "body");
  body.append(el("div", "typing", "thinking…"));
  m.append(body);
  messages.append(m);
  state.currentAssistant = body;
  scroll();
}

function appendSegment(d) {
  if (!state.currentAssistant) startAssistant();
  const body = state.currentAssistant;
  body.querySelector(".typing")?.remove();

  if (d.content_type === "code") {
    body.append(panelCode(d.content, d.metadata?.language || ""));
  } else if (d.content_type === "data") {
    body.append(panelData(d.content));
  } else {
    // Plain prose accumulates into one bubble so it reads as a paragraph.
    let bubble = body.querySelector(".bubble:last-of-type");
    if (!bubble || bubble.dataset.closed) {
      bubble = el("div", "bubble");
      body.append(bubble);
    }
    bubble.textContent += (bubble.textContent ? "\n" : "") + d.content;
  }
  scroll();
}

function panelCode(content, lang) {
  const p = el("div", "panel");
  const head = el("div", "panel-head");
  head.append(el("span", null, lang || "code"));
  const copy = el("button", null, "copy");
  copy.onclick = () => {
    navigator.clipboard.writeText(content);
    copy.textContent = "copied";
    setTimeout(() => (copy.textContent = "copy"), 1200);
  };
  head.append(copy);
  p.append(head, el("pre", null, content));
  return p;
}

function panelData(content) {
  const p = el("div", "panel");
  p.append(el("div", "panel-head", "data"));
  let rows = null;
  try { rows = JSON.parse(content); } catch { /* not JSON */ }

  if (Array.isArray(rows) && rows.length && typeof rows[0] === "object") {
    const cols = [...new Set(rows.flatMap((r) => Object.keys(r)))];
    const table = el("table");
    const thead = el("thead"), tr = el("tr");
    cols.forEach((c) => tr.append(el("th", null, c)));
    thead.append(tr);
    const tbody = el("tbody");
    rows.slice(0, 200).forEach((r) => {
      const row = el("tr");
      cols.forEach((c) => row.append(el("td", null, r[c] ?? "")));
      tbody.append(row);
    });
    table.append(thead, tbody);
    p.append(table);
  } else {
    p.append(el("pre", null, content));
  }
  return p;
}

function appendToolNote(d) {
  if (!state.currentAssistant) startAssistant();
  const bits = [`used ${d.tool}`];
  if (d.escalated) bits.push("permission raised — turn is tainted");
  if (d.is_egress) bits.push("can send data off this machine");
  const note = el("div", `tool-note${d.tainted ? " tainted" : ""}`, bits.join(" · "));
  state.currentAssistant.append(note);
  scroll();
}

function appendError(d) {
  if (!state.currentAssistant) startAssistant();
  state.currentAssistant.append(el("div", "error-note", d.error || JSON.stringify(d)));
  scroll();
}

function appendNote(text) {
  messages.append(el("div", "hint", text));
  scroll();
}

/* ── composer ──────────────────────────────────────────────────────── */

const input = $("input");

input.addEventListener("input", () => {
  input.style.height = "auto";
  input.style.height = Math.min(input.scrollHeight, 180) + "px";
});

input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    $("composer").requestSubmit();
  }
});

$("composer").addEventListener("submit", async (e) => {
  e.preventDefault();
  const text = input.value.trim();
  if (!text || state.busy) return;

  state.busy = true;
  $("send").disabled = true;
  input.value = "";
  input.style.height = "auto";

  messages.querySelector(".hint")?.remove();
  appendUser(text);
  startAssistant();

  try {
    const res = await fetch("/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, session_id: "web" }),
    });
    if (!res.ok) appendError({ error: (await res.text()) || res.statusText });
    else {
      const d = await res.json();
      if (d.tainted) {
        appendNote(`This turn read untrusted content (${d.taint_sources.join(", ")}).`);
      }
    }
  } catch (err) {
    appendError({ error: String(err) });
  } finally {
    state.currentAssistant?.querySelector(".typing")?.remove();
    state.currentAssistant = null;
    state.busy = false;
    $("send").disabled = false;
    input.focus();
    refreshMemoryBadge();
  }
});

/* ── approval dialog (DD-134, DD-135) ──────────────────────────────── */

let readDelayTimer = null;

function showApproval(d) {
  state.pendingApproval = d;

  const dialog = $("approval");
  dialog.classList.toggle("high", d.risk === "high");

  $("approval-action").textContent = d.human_action;

  const taint = $("approval-taint");
  if (d.taint_sources?.length) {
    const list = $("approval-taint-list");
    list.replaceChildren(...d.taint_sources.map((s) => el("li", null, s)));
    taint.hidden = false;
  } else taint.hidden = true;

  const dest = $("approval-dest");
  if (d.destination) {
    $("approval-dest-value").textContent = d.destination;
    $("approval-dest-unknown").hidden = d.destination_known;
    dest.hidden = false;
  } else dest.hidden = true;

  const payloadWrap = $("approval-payload-wrap");
  if (d.payload_preview) {
    // Scrollable, never truncated: truncation hides exactly what an injected
    // action would rely on.
    $("approval-payload").textContent = d.payload_preview;
    payloadWrap.hidden = false;
  } else payloadWrap.hidden = true;

  const esc = $("approval-escalation");
  if (d.escalated) {
    esc.textContent =
      `Permission raised from tier ${d.declared_tier} to ${d.effective_tier} (${d.reason}).`;
    esc.hidden = false;
  } else esc.hidden = true;

  const approve = $("approval-approve");
  if (d.risk === "high") {
    // Enforced read delay, applied only to the worst case so it does not
    // become background noise.
    let left = 1.5;
    approve.disabled = true;
    approve.textContent = `Approve in ${left.toFixed(1)}s`;
    clearInterval(readDelayTimer);
    readDelayTimer = setInterval(() => {
      left -= 0.1;
      if (left <= 0) {
        clearInterval(readDelayTimer);
        approve.disabled = false;
        approve.textContent = "Approve";
      } else approve.textContent = `Approve in ${left.toFixed(1)}s`;
    }, 100);
  } else {
    approve.disabled = false;
    approve.textContent = "Approve";
  }

  $("approval-backdrop").hidden = false;
  $("approval-deny").focus();          // Deny is the default action
}

function hideApproval() {
  clearInterval(readDelayTimer);
  $("approval-backdrop").hidden = true;
  state.pendingApproval = null;
}

async function respond(granted) {
  const d = state.pendingApproval;
  if (!d) return;
  hideApproval();
  await fetch("/approve", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      invocation_id: d.invocation_id,
      token: d.token,
      granted,
    }),
  });
}

$("approval-deny").onclick = () => respond(false);
$("approval-approve").onclick = () => respond(true);

document.addEventListener("keydown", (e) => {
  if (!state.pendingApproval) return;
  // Escape and Enter both deny. Approving requires reaching the button.
  if (e.key === "Escape") { e.preventDefault(); respond(false); }
  if (e.key === "Enter" && document.activeElement !== $("approval-approve")) {
    e.preventDefault();
    respond(false);
  }
});

/* ── memory ────────────────────────────────────────────────────────── */

async function loadMemory() {
  const data = await (await fetch("/memory")).json();
  const list = $("memory-list");
  list.replaceChildren();

  if (data.unconfirmed.length) {
    list.append(el("div", "section-title", `Needs review (${data.unconfirmed.length})`));
    data.unconfirmed.forEach((f) => list.append(factCard(f, true)));
  }

  list.append(el("div", "section-title", `Confirmed (${data.confirmed.length})`));
  if (!data.confirmed.length) {
    list.append(el("div", "hint", "Nothing remembered yet."));
  }
  data.confirmed.forEach((f) => list.append(factCard(f, false)));
}

function factCard(f, review) {
  const card = el("div", `card${review ? " review" : ""}`);
  card.append(el("div", "statement", f.statement));

  const meta = el("div", "meta");
  if (f.origin_tainted && !f.edited) {
    meta.append(el("span", "flag tainted", "untrusted origin"));
  }
  if (f.taints_on_use) {
    meta.append(el("span", "flag tainted", "taints turns when used"));
  }
  if (f.edited) meta.append(el("span", "flag", "edited by you"));
  meta.append(el("span", null, `used ${f.use_count}×`));
  card.append(meta);

  const actions = el("div", "actions");
  if (review) {
    const confirm = el("button", "primary", "Confirm");
    confirm.onclick = async () => {
      await fetch(`/memory/${f.id}/confirm`, { method: "POST" });
      loadMemory(); refreshMemoryBadge();
    };
    actions.append(confirm);
  }

  const edit = el("button", null, "Edit");
  edit.onclick = async () => {
    const next = prompt("Rewrite this fact:", f.statement);
    if (next && next.trim()) {
      await fetch(`/memory/${f.id}/edit`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ statement: next.trim() }),
      });
      loadMemory(); refreshMemoryBadge();
    }
  };

  const del = el("button", "danger", "Delete");
  del.onclick = async () => {
    await fetch(`/memory/${f.id}`, { method: "DELETE" });
    loadMemory(); refreshMemoryBadge();
  };

  actions.append(edit, del);
  card.append(actions);
  return card;
}

async function refreshMemoryBadge() {
  try {
    const data = await (await fetch("/memory")).json();
    const badge = $("memory-badge");
    badge.textContent = data.unconfirmed.length;
    badge.hidden = data.unconfirmed.length === 0;
  } catch { /* offline */ }
}

/* ── audit ─────────────────────────────────────────────────────────── */

async function loadAudit() {
  const rows = await (await fetch("/audit?limit=100")).json();
  const list = $("audit-list");
  list.replaceChildren();

  if (!rows.length) {
    list.append(el("div", "hint", "No actions recorded yet."));
    return;
  }

  rows.forEach((r) => {
    const card = el("div", "card audit-row");
    const head = el("div");
    head.append(
      el("span", `flag ${r.outcome === "ok" ? "ok" : "denied"}`, r.outcome),
      el("span", null, ` ${r.tool} `),
    );
    if (r.tainted) head.append(el("span", "flag tainted", "tainted"));
    if (r.escalated) {
      head.append(el("span", "flag escalated",
        `T${r.declared_tier}→T${r.effective_tier}`));
    }
    if (r.is_egress) head.append(el("span", "flag egress", "egress"));
    card.append(head);

    const meta = el("div", "meta");
    meta.textContent = `${r.ts.slice(0, 19).replace("T", " ")} · ${r.args}`;
    card.append(meta);

    if (r.taint_sources?.length) {
      card.append(el("div", "meta", `after reading: ${r.taint_sources.join(", ")}`));
    }
    if (r.error) card.append(el("div", "meta", r.error));

    list.append(card);
  });
}

/* ── start ─────────────────────────────────────────────────────────── */

connect();
refreshMemoryBadge();
input.focus();
