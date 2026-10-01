/* JevRev Search service worker.
 *
 * Makes the app installable and shows an honest offline page. Search is live:
 * this worker never intercepts /api and never caches documents or results.
 * Hashed static files stay on the browser/CDN HTTP cache.
 */
const SHELL = 'jev-shell-v3-garuda';

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(SHELL).then((cache) => cache.addAll(['/search/offline.html'])));
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((key) => key.startsWith('jev-shell-') && key !== SHELL).map((key) => caches.delete(key))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener('fetch', (event) => {
  const request = event.request;
  if (request.method !== 'GET') return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith('/search/api/')) return;
  if (request.mode !== 'navigate') return;

  event.respondWith(
    fetch(request).catch(async () => {
      const offline = await caches.match('/search/offline.html');
      return (
        offline ??
        new Response('JevRev Search needs a network connection to search.', {
          status: 503,
          headers: { 'content-type': 'text/plain; charset=utf-8' },
        })
      );
    }),
  );
});
