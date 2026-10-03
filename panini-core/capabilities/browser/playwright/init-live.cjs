// PANINI live view (V2-12), loaded by Playwright MCP with --init-page for every page it opens.
// - Shows Playwright's animated pointer and action titles while PANINI acts (PANINI_BROWSER_SHOW_ACTIONS=off hides it).
// - Streams JPEG frames to PANINI only while someone is watching: it asks PANINI every second and starts or stops
//   page.screencast accordingly. Frames are posted to the loopback backend with a per-session token; no port opens.
'use strict';

const FRAME_INTERVAL_MS = 250; // At most 4 fps.
const POLL_MS = 1000;

async function init({ page }) {
  const base = process.env.PANINI_LIVE_URL;
  const token = process.env.PANINI_LIVE_TOKEN;
  if (process.env.PANINI_BROWSER_SHOW_ACTIONS !== 'off') {
    await page.screencast
      .showActions({ cursor: 'pointer', duration: 500, position: 'top-right' })
      .catch(() => {});
  }
  if (!base || !token) return;
  const endpoint = `${base}/api/live/ingest/${encodeURIComponent(token)}`;
  let streaming = false;
  let lastSent = 0;
  let closed = false;
  page.on('close', () => {
    closed = true;
  });

  async function onFrame({ data, viewportWidth, viewportHeight }) {
    const now = Date.now();
    if (now - lastSent < FRAME_INTERVAL_MS) return;
    lastSent = now;
    let title = '';
    try {
      title = (await page.title()).slice(0, 300);
    } catch {}
    await fetch(endpoint, {
      method: 'POST',
      body: data,
      headers: {
        'content-type': 'image/jpeg',
        'x-live-url': encodeURI(page.url()).slice(0, 2048),
        'x-live-title': encodeURIComponent(title),
        'x-live-width': String(viewportWidth),
        'x-live-height': String(viewportHeight),
      },
    }).catch(() => {});
  }

  async function poll() {
    while (!closed) {
      try {
        const response = await fetch(`${endpoint}/watch`);
        const { watched } = response.ok ? await response.json() : { watched: false };
        if (watched && !streaming) {
          await page.screencast.start({ size: { width: 960, height: 600 }, quality: 60, onFrame });
          streaming = true;
        } else if (!watched && streaming) {
          await page.screencast.stop();
          streaming = false;
        }
      } catch {}
      await new Promise((resolve) => setTimeout(resolve, POLL_MS));
    }
  }
  poll(); // Never awaited: the page's tools must not wait for live view.
}

module.exports = { default: init };
