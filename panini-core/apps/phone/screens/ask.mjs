import * as db from '../lib/db.mjs';
// The phone app's screens (V2-15 §5.2): Home, Inbox, Ask, Live and Settings, plus pairing.
import { card } from '../lib/cards.mjs';
import { pair, parsePairing } from '../lib/fabric.mjs';
import { action, h, toast, when } from './ui.mjs';
import { addressedRequest } from '../lib/persona.mjs';

// Ask ----------------------------------------------------------------------------------------------
export async function ask(app, shared) {
  const metadata = !shared ? await app.fabric.request('GET', '/personas') : null;
  const personas = metadata?.status === 200 && metadata.body.enabled ? metadata.body.personas : [];
  const chooser = personas.length
    ? h(
        'select',
        { 'aria-label': 'Work with' },
        personas.map((p) => h('option', { value: p.id, text: p.name })),
      )
    : null;
  if (chooser) chooser.value = metadata.body.default_id;
  const box = h('textarea', {
    'aria-label': 'Ask PANINI',
    rows: 5,
    placeholder: 'Ask PANINI to do something…',
    maxlength: shared ? 1000 : 4000,
  });
  box.value = shared ? '' : (await db.get('ask-draft')) || '';
  box.addEventListener('input', () => {
    if (!shared)
      void db.set('ask-draft', box.value).catch(() => toast('Could not save your draft.', 'warn'));
  });
  const send = async () => {
    const text = box.value.trim();
    if (!text && !shared) return;
    const request = chooser ? addressedRequest(text, chooser.value) : text;
    if (request.length > 4000)
      throw new Error('Shorten your request to leave room for the specialist name.');
    const reply = shared
      ? await app.fabric.request('POST', '/share', { ...shared, note: text })
      : await app.fabric.request('POST', '/ask', { text: request });
    if (reply.status === 200) {
      toast(shared ? 'Sent to PANINI.' : 'PANINI is on it. Approvals will come to your Inbox.');
      if (!shared) await db.remove('ask-draft');
      app.shared = null;
      app.show('home');
    } else toast(reply.body?.detail || 'Could not send.', 'warn');
  };
  return h(
    'section.screen',
    {},
    h('h2', { text: shared ? 'Shared with PANINI' : 'Ask' }),
    shared
      ? h(
          'div.card.shared',
          {},
          h('strong', { text: shared.title || shared.url || 'Shared item' }),
          h('p.muted', { text: (shared.text || shared.url || '').slice(0, 400) }),
        )
      : null,
    shared ? h('p.muted', { text: 'What should PANINI do with it? (Optional.)' }) : null,
    chooser ? h('label', {}, 'Work with', chooser) : null,
    box,
    h('button.primary.wide', { onclick: (event) => action(event.currentTarget, send) }, 'Send'),
  );
}
