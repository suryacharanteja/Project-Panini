// The phone app's screens (V2-15 §5.2): Home, Inbox, Ask, Live and Settings, plus pairing.
import { card } from '../lib/cards.mjs';
import { pair, parsePairing } from '../lib/fabric.mjs';
import { action, h, toast, when } from './ui.mjs';

// Inbox --------------------------------------------------------------------------------------------
export async function inbox(app) {
  const list = (await app.fabric.request('GET', '/interrupts')).body?.interrupts || [];
  const notices = (await app.fabric.request('GET', '/notices')).body?.notices || [];
  app.setBadge(list.length);
  const cards = [];
  for (const item of list) {
    // Restore before deciding: show the full request (with its nonce and action digest) the phone will sign.
    const full = (await app.fabric.request('GET', `/interrupts/${item.id}`)).body || item;
    cards.push(interruptCard(app, full));
  }
  return h(
    'section.screen',
    {},
    h('h2', { text: 'Inbox' }),
    cards.length ? cards : h('p.empty', { text: 'Nothing needs you right now.' }),
    notices.length ? h('h3', { text: 'Notices' }) : null,
    notices
      .slice(0, 20)
      .map((n) =>
        h(
          'div.card.notice',
          {},
          h('strong', { text: n.title || 'Notice' }),
          h('p.muted', { text: n.body || n.message || '' }),
        ),
      ),
  );
}

function interruptCard(app, view) {
  const model = card(view);
  let answer = '';
  let deciding = false;
  const act = async (button) => {
    if (deciding) return;
    deciding = true;
    try {
      const result = await app.fabric.decide(view, button, answer);
      toast(result.ok ? 'Done.' : result.message, result.ok ? '' : 'warn');
      await app.show('inbox');
    } finally {
      deciding = false;
    }
  };
  const labels = { approve: 'Approve', decline: 'Decline', done: "I'm done", answer: 'Send' };
  return h(
    'article.card.interrupt',
    { 'data-level': model.level },
    h('p.eyebrow', { text: `${view.kind} · ${model.level}` }),
    h('h3', { text: model.title }),
    model.message ? h('p', { text: model.message }) : null,
    view.preview && Object.keys(view.preview).length
      ? h(
          'details',
          {},
          h('summary', { text: 'Details' }),
          h('pre', { text: JSON.stringify(view.preview, null, 2).slice(0, 2000) }),
        )
      : null,
    model.asks
      ? h('input', {
          'aria-label': 'Your answer',
          placeholder: 'Your answer',
          oninput: (e) => (answer = e.target.value),
        })
      : null,
    model.note ? h('p.note', { text: model.note }) : null,
    h(
      'div.actions',
      {},
      model.buttons.map((b) =>
        h(
          `button${b === 'approve' || b === 'done' ? '.primary' : ''}`,
          { onclick: (event) => action(event.currentTarget, () => act(b)) },
          labels[b],
        ),
      ),
    ),
  );
}
