// The phone app's screens (V2-15 §5.2): Home, Inbox, Ask, Live and Settings, plus pairing.
import { card } from '../lib/cards.mjs';
import { pair, parsePairing } from '../lib/fabric.mjs';
import { action, h, toast, when } from './ui.mjs';

// Settings -----------------------------------------------------------------------------------------
export async function settings(app) {
  const me = (await app.fabric.request('GET', '/devices/self')).body || {};
  return h(
    'section.screen',
    {},
    h('h2', { text: 'Settings' }),
    h(
      'div.card',
      {},
      h('strong', { text: me.name || 'This phone' }),
      h('p.muted', { text: `Paired ${when(me.created)}` }),
    ),
    h(
      'div.card',
      {},
      h('h3', { text: 'Notifications' }),
      h('p.muted', {
        text: 'Approvals arrive on your lock screen. On Android you can approve from the notification.',
      }),
      h('button.wide', { onclick: () => app.enablePush() }, 'Turn on notifications'),
    ),
    h(
      'div.card',
      {},
      h('h3', { text: 'Forget this phone' }),
      h('p.muted', { text: 'Its keys stop working at once. You can pair again later.' }),
      h(
        'button.danger.wide',
        {
          onclick: async () => {
            if (!confirm('Forget this phone?')) return;
            await app.fabric.forget();
            location.replace('/');
          },
        },
        'Forget this phone',
      ),
    ),
  );
}
