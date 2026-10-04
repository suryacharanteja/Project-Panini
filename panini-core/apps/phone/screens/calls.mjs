import { h, action, toast } from './ui.mjs';
import { personaChip } from './persona.mjs';

export async function calls(app) {
  const revision = app.viewRevision;
  const panel = h('div.card');
  const view = h(
    'section.screen',
    {},
    h('h2', { text: 'Phone calls' }),
    panel,
    h('button.wide', { onclick: () => app.show('home') }, 'Back to Home'),
  );
  async function refresh() {
    const reply = await app.fabric.request('GET', '/calls');
    if (reply.status !== 200) throw new Error(reply.body?.detail || 'Call status is unavailable.');
    const active = reply.body.active;
    panel.replaceChildren(h('h3', { text: active ? 'Call connected' : 'No active call' }));
    if (active) {
      panel.append(
        personaChip(active.persona),
        h('p', { text: active.number }),
        h('p.muted', {
          text: active.verified
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
      panel.append(h('h3', { text: 'Live transcript' }));
      for (const message of active.transcript || [])
        panel.append(h('p', { text: `${message.role}: ${message.text}` }));
    }
    for (const call of reply.body.calls || [])
      panel.append(
        h('p.muted', {
          text: `${call.state} · ${Math.round(call.duration_s)} seconds · ${call.reason || ''}`,
        }),
        call.summary ? h('p', { text: call.summary }) : null,
      );
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
