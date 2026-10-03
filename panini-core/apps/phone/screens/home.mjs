// The phone app's screens (V2-15 §5.2): Home, Inbox, Ask, Live and Settings, plus pairing.
import { card } from '../lib/cards.mjs';
import { pair, parsePairing } from '../lib/fabric.mjs';
import { action, h, toast, when } from './ui.mjs';

// Home ---------------------------------------------------------------------------------------------
export async function home(app) {
  const reply = await app.fabric.request('GET', '/status');
  const status = reply.body || {};
  const paused = Boolean(status.paused);
  return h(
    'section.screen',
    {},
    h(
      'div.card.hero',
      {},
      h('p.eyebrow', { text: 'PANINI' }),
      h('h2', { text: paused ? 'Paused' : 'Ready' }),
      h('p.muted', {
        text: paused ? 'Nothing runs until you resume it on the PC.' : 'Your PC is online.',
      }),
      h(
        'button.danger.wide',
        {
          disabled: paused,
          onclick: (event) =>
            action(event.currentTarget, async () => {
              const reply = await app.fabric.request('POST', '/pause');
              if (reply.status !== 200)
                throw new Error(reply.body?.detail || 'Could not pause PANINI.');
              toast('PANINI is paused.');
              app.show('home');
            }),
        },
        paused ? 'Paused' : 'Pause PANINI',
      ),
    ),
    h(
      'div.card',
      {},
      h('h3', { text: 'Background work' }),
      h('p.muted', {
        text: `${status.tasks?.running ?? status.running ?? 0} running, ${status.tasks?.queued ?? status.queued ?? 0} waiting`,
      }),
    ),
  );
}
