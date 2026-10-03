// The phone app's screens (V2-15 §5.2): Home, Inbox, Ask, Live and Settings, plus pairing.
import { card } from '../lib/cards.mjs';
import { pair, parsePairing } from '../lib/fabric.mjs';
import { action, h, toast, when } from './ui.mjs';

// Pairing ------------------------------------------------------------------------------------------
export function pairing(app) {
  const link = parsePairing(location.hash);
  if (!link) {
    return h(
      'section.screen',
      {},
      h(
        'div.card.hero',
        {},
        h('h2', { text: 'Pair with your PC' }),
        h('p.muted', {
          text: 'On your PC, open PANINI Admin, Remote access, and make a pairing code. Then scan it or open its link here.',
        }),
      ),
    );
  }
  const name = h('input', {
    value: /Android/.test(navigator.userAgent) ? 'Android phone' : 'Phone',
    maxlength: 60,
    'aria-label': 'Phone name',
  });
  const out = h('div', {});
  const start = async () => {
    try {
      const sas = await pair(link, location.origin, name.value || 'Phone');
      history.replaceState(null, '', '/'); // The code is single use; do not keep it in the address bar.
      out.replaceChildren(
        h('p', {
          text: 'Check that your PC shows the same six digits, then press "The digits match" there:',
        }),
        h('p.sas', { text: sas }),
        h('p.muted', { text: 'Waiting for your PC…' }),
      );
      await app.waitForConfirmation();
    } catch (error) {
      out.replaceChildren(h('p.warn', { text: error.message }));
    }
  };
  return h(
    'section.screen',
    {},
    h(
      'div.card.hero',
      {},
      h('h2', { text: 'Pair with your PC' }),
      h('p.muted', { text: `PC ${link.pc.id.slice(0, 8)}…` }),
      name,
      h(
        'button.primary.wide',
        { onclick: (event) => action(event.currentTarget, start) },
        'Pair this phone',
      ),
      out,
    ),
  );
}
