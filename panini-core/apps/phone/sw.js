// PANINI phone app service worker (V2-15 §5.4-5.5): app-shell cache, push notifications with Approve/Decline on
// Android, decisions signed by the device key without opening the app, and Android's share sheet.
import { Fabric } from './lib/fabric.mjs';
import * as db from './lib/db.mjs';

const CACHE = 'panini-phone-v6';
const SHELL = [
  '/',
  '/app.js',
  '/app.css',
  '/manifest.webmanifest',
  '/lib/envelope.mjs',
  '/lib/fabric.mjs',
  '/lib/socket.mjs',
  '/lib/cards.mjs',
  '/lib/db.mjs',
  '/lib/persona.mjs',
  '/screens/persona.mjs',
  '/screens/calls.mjs',
  '/screens/home.mjs',
  '/screens/threads.mjs',
  '/screens/inbox.mjs',
  '/screens/ask.mjs',
  '/screens/live.mjs',
  '/screens/settings.mjs',
  '/screens/pair.mjs',
  '/screens/screens.mjs',
  '/screens/ui.mjs',
  '/icons/icon-192.png',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches
      .open(CACHE)
      .then((cache) => cache.addAll(SHELL))
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);
  if (event.request.method === 'POST' && url.pathname === '/share')
    return event.respondWith(share(event.request));
  if (event.request.method !== 'GET' || url.origin !== location.origin) return;
  if (!SHELL.includes(url.pathname) && event.request.mode !== 'navigate') return;
  // Network first (the PC is the source of truth), the cached shell when offline.
  event.respondWith(
    fetch(event.request).catch(() =>
      caches.match(event.request).then((hit) => hit || caches.match('/')),
    ),
  );
});

async function share(request) {
  const form = await request.formData();
  await db.set('shared', {
    title: String(form.get('title') || '').slice(0, 300),
    text: String(form.get('text') || '').slice(0, 8000),
    url: String(form.get('url') || '').slice(0, 2048),
  });
  return Response.redirect(new URL('/?tab=ask', self.location.origin).href, 303);
}

self.addEventListener('push', (event) => {
  let data = {};
  try {
    data = event.data.json();
  } catch {}
  const isTap = data.type === 'interrupt' && data.level === 'tap';
  event.waitUntil(
    self.registration.showNotification(data.title || 'PANINI', {
      body:
        data.type === 'interrupt'
          ? data.level === 'tap'
            ? 'Approve or decline.'
            : 'Open PANINI to decide.'
          : '',
      tag: data.id || 'panini',
      data,
      icon: '/icons/icon-192.png',
      badge: '/icons/icon-192.png',
      requireInteraction: data.type === 'interrupt',
      actions: isTap
        ? [
            { action: 'approve', title: 'Approve' },
            { action: 'decline', title: 'Decline' },
          ]
        : [],
    }),
  );
});

self.addEventListener('notificationclick', (event) => {
  const data = event.notification.data || {};
  event.notification.close();
  if (data.type === 'interrupt' && (event.action === 'approve' || event.action === 'decline')) {
    event.waitUntil(decideFromNotification(data, event.action));
    return;
  }
  event.waitUntil(openApp(data.type === 'interrupt' ? '/?tab=inbox' : '/'));
});

async function decideFromNotification(data, button) {
  // Restore before deciding: fetch the request again, then sign exactly that, with the device key.
  try {
    const fabric = await Fabric.load();
    if (!fabric) throw new Error('This phone is not paired.');
    const current = await fabric.request('GET', `/interrupts/${data.id}`);
    if (current.status !== 200) throw new Error('This request is no longer available.');
    if (current.body.level !== 'tap') throw new Error('Open PANINI to review this decision.');
    const result = await fabric.decide(current.body, button);
    await self.registration.showNotification(
      result.ok ? (button === 'approve' ? 'Approved' : 'Declined') : 'Not done',
      {
        body: result.ok ? data.title : result.message,
        tag: `${data.id}-result`,
        icon: '/icons/icon-192.png',
      },
    );
  } catch (error) {
    await self.registration.showNotification('Open PANINI to decide', {
      body: error.message,
      tag: `${data.id}-result`,
    });
  }
}

async function openApp(path) {
  const windows = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
  for (const client of windows) {
    if ('focus' in client) {
      await client.navigate(path).catch(() => {});
      return client.focus();
    }
  }
  return self.clients.openWindow(path);
}
