import { randomUUID } from 'node:crypto';

export const MAX_BODY_BYTES = 15 * 1024 * 1024;
const PUBLIC_DOCUMENTS = new Set([
  '/',
  '/index.html',
  '/browser',
  '/browser-use',
  '/browser-use.html',
  '/browser-demo.html',
  '/activity',
  '/search',
  '/search/',
  '/search/results',
]);

export const EXTENSION_ORIGIN = /^((chrome|moz)-extension):\/\/[a-z0-9-]+$/;

export function allowedRequest(req, allowedOrigins = []) {
  let host;
  try {
    host = new URL(`http://${req.headers.host}`).hostname;
  } catch {
    return false;
  }
  const configuredHosts = allowedOrigins.map((origin) => new URL(origin).hostname);
  if (!['localhost', '127.0.0.1', '[::1]', ...configuredHosts].includes(host)) return false;
  // Browser links and extension tab navigation may be cross-site. Permit only
  // top-level GETs to public documents; API calls, frames and fetches retain
  // the origin checks below. Host validation still prevents DNS rebinding.
  if (
    req.method === 'GET' &&
    PUBLIC_DOCUMENTS.has(req.path) &&
    req.headers['sec-fetch-mode'] === 'navigate' &&
    req.headers['sec-fetch-dest'] === 'document'
  )
    return true;
  const origin = req.headers.origin;
  // The one-time pairing code is the authority for extension pairing only.
  if (req.path === '/api/extension/pair' && EXTENSION_ORIGIN.test(origin || '')) return true;
  if (origin && allowedOrigins.includes(origin)) return true;
  if (req.headers['sec-fetch-site'] === 'cross-site') return false;
  return !origin || origin === `http://${req.headers.host}`;
}

export function secureRequests(allowedOrigins) {
  return (req, res, next) => {
    req.requestId = randomUUID();
    res.setHeader('X-Request-ID', req.requestId);
    res.setHeader('X-Content-Type-Options', 'nosniff');
    res.setHeader('Referrer-Policy', 'same-origin');
    res.setHeader('X-Frame-Options', 'DENY');
    if (req.path.startsWith('/api/') || req.path.startsWith('/auth/'))
      res.setHeader('Cache-Control', 'no-store');
    if (!allowedRequest(req, allowedOrigins))
      return res.status(403).json({ error: 'Origin is not allowed' });
    const length = req.headers['content-length'];
    if (length && (!/^\d+$/.test(length) || Number(length) > MAX_BODY_BYTES)) {
      return res.status(413).json({ error: 'Request is too large' });
    }
    next();
  };
}

/** Shared across HTTP and WebSocket entry points, with an injectable clock. */
export function createRateLimiter(limit = 60, now = Date.now) {
  let window = -1;
  let count = 0;
  return {
    take() {
      const current = Math.floor(now() / 60000);
      if (window !== current) {
        window = current;
        count = 0;
      }
      return ++count <= limit;
    },
    retryAfter() {
      return Math.max(1, Math.ceil((60000 - (now() % 60000)) / 1000));
    },
  };
}

export function limitRequests(limiter) {
  return (req, res, next) => {
    if (
      req.method === 'POST' &&
      ![
        '/api/browser/stop',
        '/api/browser/reset',
        '/api/voice/stop',
        '/api/voice/end',
        '/api/voice/events',
      ].includes(req.path) &&
      !limiter.take()
    ) {
      res.setHeader('Retry-After', String(limiter.retryAfter()));
      return res.status(429).json({ error: 'Too many requests. Try again shortly.' });
    }
    next();
  };
}

export function createStreamLimiter(limit = 4) {
  let active = 0;
  return {
    get active() {
      return active;
    },
    middleware(req, res, next) {
      if (req.method !== 'POST') return next();
      if (active >= limit) {
        res.setHeader('Retry-After', '1');
        return res.status(429).json({ error: 'Search is busy. Please retry shortly.' });
      }
      active++;
      let released = false;
      const release = () => {
        if (!released) {
          released = true;
          active--;
        }
      };
      res.once('finish', release);
      res.once('close', release);
      next();
    },
  };
}
