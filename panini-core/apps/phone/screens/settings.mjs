// The phone app's screens (V2-15 §5.2): Home, Inbox, Ask, Live and Settings, plus pairing.
import { card } from '../lib/cards.mjs';
import { pair, parsePairing } from '../lib/fabric.mjs';
import { action, h, toast, when } from './ui.mjs';
import { enroll } from '../lib/passkeys.mjs';

// Settings -----------------------------------------------------------------------------------------
export async function settings(app) {
  const me = (await app.fabric.request('GET', '/devices/self')).body || {};
  const keys = (await app.fabric.request('GET', '/passkeys')).body?.passkeys || [];
  return h(
    'section.screen',
    {},
    h('h2', { text: 'Settings' }),
    h('button.wide', { onclick: () => app.show('spaces') }, 'Open notebook'),
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
      h('h3', { text: 'Passkeys' }),
      h('p.muted', {
        text: 'Verify sensitive decisions using your device screen lock. Requires a stable HTTPS app address.',
      }),
      h(
        'button.wide',
        {
          onclick: (event) =>
            action(event.currentTarget, async () => {
              await enroll(app.fabric);
              toast('Passkey added.');
              await app.show('settings');
            }),
        },
        'Add passkey',
      ),
      keys.map((key) =>
        h(
          'div',
          {},
          h('p.muted', { text: `Added ${when(key.created)} · ${key.origin}` }),
          h(
            'button',
            {
              onclick: (event) =>
                action(event.currentTarget, async () => {
                  if (!confirm('Remove this passkey from this phone?')) return;
                  const reply = await app.fabric.request(
                    'DELETE',
                    `/passkeys/${key.credential_id}`,
                  );
                  if (reply.status !== 200) throw new Error('Could not remove passkey.');
                  await app.show('settings');
                }),
            },
            'Remove passkey',
          ),
        ),
      ),
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
