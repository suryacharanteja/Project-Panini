import { h, action, toast } from './ui.mjs';
import { personaChip } from './persona.mjs';
import { saveSource } from './spaces.mjs';
import { callReport } from './call-report.mjs';

export async function calls(app) {
  const revision = app.viewRevision;
  const panel = h('div.card');
  const savedSources = new Map();
  let ownerNote = '';
  const view = h(
    'section.screen',
    {},
    h('h2', { text: 'Phone calls' }),
    panel,
    h('button.wide', { onclick: () => app.show('home') }, 'Back to Home'),
  );
  async function refresh() {
    if (document.activeElement?.classList.contains('outbound-note')) return;
    const reply = await app.fabric.request('GET', '/calls');
    if (reply.status !== 200) throw new Error(reply.body?.detail || 'Call status is unavailable.');
    const outbound = await app.fabric.request('GET', '/outbound');
    if (outbound.status !== 200)
      throw new Error(outbound.body?.detail || 'Outbound status is unavailable.');
    const active = reply.body.active;
    panel.replaceChildren(h('h3', { text: active ? 'Call connected' : 'No active call' }));
    if (active) {
      panel.append(
        personaChip(active.persona),
        h('p', { text: active.number }),
        h('p.muted', {
          text:
            active.direction === 'outbound'
              ? `Outbound call · ${active.state}. Review additional decisions in Inbox.`
              : active.verified
                ? 'Caller verified'
                : 'Waiting for keypad PIN or phone confirmation',
        }),
      );
      for (const [name, label] of [
        ['verify', 'Verify this is me'],
        ['transfer', 'Transfer to my phone'],
        ['end', 'End call'],
      ]) {
        if (name === 'verify' && active.verified) continue;
        panel.append(
          h(
            'button.wide',
            {
              onclick: (event) =>
                action(event.currentTarget, async () => {
                  const restored = await app.fabric.request('GET', '/calls');
                  if (restored.body?.active?.id !== active.id)
                    throw new Error('This call has ended or changed.');
                  const result = await app.fabric.request(
                    'POST',
                    `/calls/${encodeURIComponent(active.id)}/${name}`,
                  );
                  if (result.status !== 200)
                    throw new Error(result.body?.detail || 'Call control failed.');
                  toast(
                    name === 'verify' ? 'Open Inbox to confirm the caller.' : 'Call control sent.',
                  );
                  if (name === 'verify') app.show('inbox');
                  else await refresh();
                }),
            },
            label,
          ),
        );
      }
      if (active.direction === 'outbound') {
        const note = h('textarea.outbound-note', {
          'aria-label': 'Owner call note',
          maxLength: 500,
          value: ownerNote,
          oninput: (event) => (ownerNote = event.target.value),
        });
        note.value = ownerNote;
        panel.append(
          h('label', { text: 'Owner call note' }, note),
          h(
            'button.wide',
            {
              onclick: (event) =>
                action(event.currentTarget, async () => {
                  const restored = await app.fabric.request('GET', '/calls');
                  if (restored.body?.active?.id !== active.id)
                    throw new Error('This call has ended or changed.');
                  const result = await app.fabric.request(
                    'POST',
                    `/outbound/${encodeURIComponent(active.id)}/note`,
                    { text: ownerNote },
                  );
                  if (result.status !== 200)
                    throw new Error(result.body?.detail || 'The note was refused.');
                  ownerNote = '';
                  note.value = '';
                  toast('Owner note sent.');
                }),
            },
            'Send owner note',
          ),
        );
      }
      panel.append(h('h3', { text: 'Live transcript' }));
      for (const message of active.transcript || [])
        panel.append(h('p', { text: `${message.role}: ${message.text}` }));
    }
    for (const call of reply.body.calls || []) {
      if (call.receipt_id && !savedSources.has(call.receipt_id))
        savedSources.set(call.receipt_id, saveSource(app, 'receipt', call.receipt_id));
      panel.append(
        h('p.muted', {
          text: `${call.state} · ${Math.round(call.duration_s)} seconds · ${call.reason || ''}`,
        }),
        call.summary ? h('p', { text: call.summary }) : null,
        call.direction === 'outbound'
          ? h('p.muted', {
              text: `Voice usage: ${call.final_usage_seconds != null ? `${call.final_usage_seconds} seconds (final provider report)` : call.usage_seconds != null ? `${call.usage_seconds} seconds observed; final usage unconfirmed` : 'unavailable'}. Session finalization ${call.finalization_confirmed ? 'confirmed' : 'unconfirmed'}. Cost unavailable.`,
            })
          : null,
        call.receipt_id ? savedSources.get(call.receipt_id) : null,
        call.outcome_report ? callReport(app, call.id, call.outcome_report) : null,
      );
    }
    const pending = (outbound.body.briefs || []).filter((row) =>
      ['review', 'scheduled'].includes(row.state),
    );
    if (pending.length) panel.append(h('h3', { text: 'Pending outbound calls' }));
    for (const row of pending) {
      panel.append(
        h('p', { text: `${row.brief.callee_name} · ${row.state}` }),
        h('p.muted', {
          text: row.schedule
            ? `Reviewed time: ${new Date(row.schedule.not_before * 1000).toLocaleString()} (${Intl.DateTimeFormat().resolvedOptions().timeZone}). Start before ${new Date(row.schedule.latest_start * 1000).toLocaleString()}. Calling hours apply to the destination. Missed windows cancel; no retry.`
            : 'Review the full brief in Inbox. One attempt only within calling hours.',
        }),
        h(
          'button.wide',
          {
            onclick: (event) =>
              action(event.currentTarget, async () => {
                const restored = await app.fabric.request('GET', '/outbound');
                const current = restored.body?.briefs?.find((item) => item.id === row.id);
                if (
                  restored.status !== 200 ||
                  !current ||
                  !['review', 'scheduled'].includes(current.state) ||
                  current.digest !== row.digest
                )
                  throw new Error(
                    'This pending call has ended or changed. Refresh before cancelling.',
                  );
                const result = await app.fabric.request(
                  'POST',
                  `/outbound/${encodeURIComponent(row.id)}/cancel`,
                );
                if (result.status !== 200)
                  throw new Error(result.body?.detail || 'Cancellation failed.');
                toast('Pending call cancelled.');
                await refresh();
              }),
          },
          `Cancel pending call to ${row.brief.callee_name}`,
        ),
      );
    }
  }
  await refresh();
  async function poll() {
    if (app.viewRevision !== revision || !view.isConnected) return;
    try {
      await refresh();
    } catch (error) {
      panel.replaceChildren(h('p', { text: error.message }));
    }
    if (app.viewRevision === revision) setTimeout(poll, 2500);
  }
  setTimeout(poll, 2500);
  return view;
}
