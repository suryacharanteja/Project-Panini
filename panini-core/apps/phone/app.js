// PANINI on your phone (V2-15): an installable app (Android first) that talks to your PC over the fabric.
import { Fabric } from './lib/fabric.mjs';
import * as db from './lib/db.mjs';
import { b64 } from './lib/envelope.mjs';
import * as screens from './screens/screens.mjs';
import { h, toast } from './screens/ui.mjs';

const TABS = [
  ['home', 'Home'],
  ['inbox', 'Inbox'],
  ['ask', 'Ask'],
  ['threads', 'Work'],
  ['live', 'Live'],
  ['settings', 'Settings'],
];

const app = {
  fabric: null,
  shared: null,
  viewRevision: 0,
  installPrompt: null,
  main: document.querySelector('main'),
  nav: document.querySelector('nav'),

  async show(name) {
    if (name !== 'calls' && !TABS.some(([tab]) => tab === name)) name = 'home';
    const activeTab = name === 'calls' ? 'home' : name;
    const revision = ++this.viewRevision;
    this.main.setAttribute('aria-busy', 'true');
    this.nav.querySelectorAll('button').forEach((b) => {
      b.classList.toggle('active', b.dataset.tab === activeTab);
      if (b.dataset.tab === activeTab) b.setAttribute('aria-current', 'page');
      else b.removeAttribute('aria-current');
    });
    try {
      const view = await screens[name](this, name === 'ask' ? this.shared : undefined);
      if (revision !== this.viewRevision) return;
      this.main.replaceChildren(view);
    } catch (error) {
      if (revision !== this.viewRevision) return;
      if (error.revoked) return this.revoked();
      this.main.replaceChildren(
        h(
          'section.screen',
          {},
          h(
            'div.card.hero',
            {},
            h('h2', { text: 'Your PC is offline' }),
            h('p.muted', { text: error.message }),
            h('button.wide', { onclick: () => this.show(name) }, 'Try again'),
          ),
        ),
      );
    } finally {
      if (revision === this.viewRevision) this.main.removeAttribute('aria-busy');
    }
  },

  setBadge(count) {
    const tab = this.nav.querySelector('[data-tab="inbox"]');
    tab.dataset.badge = count ? String(count) : '';
    navigator.setAppBadge?.(count || undefined).catch(() => {});
  },

  async revoked() {
    ++this.viewRevision;
    this.fabric?.socket?.close();
    this.fabric = null;
    this.nav.hidden = true;
    this.main.removeAttribute('aria-busy');
    await db.remove('device');
    await db.remove('pc');
    this.main.replaceChildren(
      h(
        'section.screen',
        {},
        h(
          'div.card.hero',
          {},
          h('h2', { text: 'This phone was removed from PANINI' }),
          h('p.muted', { text: 'Its keys were deleted. Pair again from PANINI Admin on your PC.' }),
          h(
            'button.wide',
            { onclick: () => this.main.replaceChildren(screens.pairing(this)) },
            'Pair again',
          ),
        ),
      ),
    );
  },

  /** After pairing: try to open a session until the person confirms the digits on the PC. */
  async waitForConfirmation() {
    for (let i = 0; i < 120; i++) {
      const fabric = await Fabric.load();
      try {
        await fabric.connect();
        this.fabric = fabric;
        toast('Paired. Welcome.');
        this.nav.hidden = false;

        return this.show('home');
      } catch {
        await new Promise((r) => setTimeout(r, 2500));
      }
    }
    throw new Error('The PC did not confirm in five minutes. Pair again.');
  },

  async enablePush(quiet = false) {
    try {
      if (!('serviceWorker' in navigator) || !('PushManager' in window))
        throw new Error('Notifications are not supported here.');
      if ((await Notification.requestPermission()) !== 'granted')
        throw new Error('Notifications are blocked for PANINI.');
      const key = (await this.fabric.request('GET', '/push/key')).body?.key;
      const registration = await navigator.serviceWorker.ready;
      const raw = Uint8Array.from(
        atob(key.replace(/-/g, '+').replace(/_/g, '/') + '='.repeat((4 - (key.length % 4)) % 4)),
        (c) => c.charCodeAt(0),
      );
      const subscription = await registration.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: raw,
      });
      const json = subscription.toJSON();
      await this.fabric.request('POST', '/push/subscribe', {
        endpoint: json.endpoint,
        p256dh: json.keys.p256dh,
        auth: json.keys.auth,
      });
      if (!quiet) toast('Notifications are on.');
    } catch (error) {
      if (!quiet) toast(error.message, 'warn');
    }
  },
};

window.addEventListener('beforeinstallprompt', (event) => {
  event.preventDefault();
  app.installPrompt = event;
  const button = document.querySelector('#install');
  button.hidden = false;
  button.onclick = async () => {
    const prompt = app.installPrompt;
    if (!prompt) return;
    await prompt.prompt();
    await prompt.userChoice;
    app.installPrompt = null;
    button.hidden = true;
  };
});
window.addEventListener('appinstalled', () => {
  document.querySelector('#install').hidden = true;
});

async function start() {
  if ('serviceWorker' in navigator)
    navigator.serviceWorker.register('/sw.js', { type: 'module', scope: '/' }).catch(() => {});
  app.nav.replaceChildren(
    ...TABS.map(([tab, label]) =>
      h('button', { 'data-tab': tab, onclick: () => app.show(tab) }, label),
    ),
  );
  app.fabric = await Fabric.load();
  const params = new URLSearchParams(location.search);
  if (!app.fabric || location.hash.includes('v=1')) {
    app.nav.hidden = true;
    return app.main.replaceChildren(screens.pairing(app));
  }
  const pending = await db.get('shared'); // Captured by the service worker from Android's share sheet.
  if (pending) {
    app.shared = pending;
    await db.remove('shared');
    return app.show('ask');
  }
  return app.show(params.get('tab') || 'home');
}

window.paniniDebug = { b64 }; // Lets the PC-side tests check the bundle loaded.
start().catch((error) => {
  app.main.replaceChildren(
    h(
      'section.screen',
      {},
      h('h2', { text: 'PANINI could not start' }),
      h('p', { text: error.message }),
      h('button', { onclick: () => location.reload() }, 'Try again'),
    ),
  );
});
