/**
 * The one local front door for every PANINI surface:
 * - /api/panini/*   the PANINI shell's coordinator API (its own bearer/cookie credentials)
 * - /ws/extension   the paired browser extension (mutually authenticated by the backend)
 * - everything else the web console, which requires a signed-in console session
 */
import express from 'express';
import { existsSync } from 'node:fs';
import { createServer } from 'node:http';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  secureRequests,
  createRateLimiter,
  createStreamLimiter,
  limitRequests,
} from './security.mjs';
import { proxy } from './proxy.mjs';
import { attachComparisonSocket } from './comparison-socket.mjs';
import { attachExtensionSocket } from './extension-socket.mjs';
import { forwardToSignin } from './signin-gate.mjs';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..', '..');
const BACKEND_ROUTES = new Set([
  'GET /api/tasks',
  'POST /api/compare',
  'GET /api/browser/status',
  'GET /api/browser/extension/status',
  'POST /api/browser/run-stream',
  'POST /api/browser/stop',
  'POST /api/browser/reset',
  'GET /api/stt/status',
  'POST /api/stt/transcribe',
  'POST /api/stt/transcribe-base64',
  'GET /api/voice/status',
  'POST /api/voice/session',
  'POST /api/voice/events',
  'POST /api/voice/stop',
  'POST /api/voice/end',
  'POST /api/voice/console',
  'POST /api/voice/turn',
  'POST /api/extension/pairing-code',
  'POST /api/extension/pair',
  'GET /api/extension/devices',
  'POST /api/console/panini-observer',
  'POST /a2a',
  'GET /.well-known/agent-card.json',
]);
const BACKEND_PREFIXES = [
  ['DELETE', '/api/extension/devices/'],
  // Harness: memories, skills, background tasks and reviews.
  ...['GET', 'POST', 'PUT', 'PATCH', 'DELETE'].map((method) => [method, '/api/harness/']),
  // PANINI Admin API.
  ...['GET', 'POST', 'PUT', 'PATCH', 'DELETE'].map((method) => [method, '/api/admin/v1/']),
  // AG-UI agent runs (V2-07): server-sent event streams.
  ['POST', '/api/agui/'],
];
// Carry their own credentials (or none, for health); everything else is a console route.
const OPEN_API = new Set([
  '/api/health',
  '/api/extension/pair',
  // Agent-to-agent: callers present their own A2A bearer tokens.
  '/a2a',
  '/.well-known/agent-card.json',
]);
// Pages anyone on this computer may open: the sign-in help and a sample site for the agent.
const OPEN_PAGES = new Set(['/auth-required.html', '/browser-demo.html']);
// Sign-in page, its assets and its APIs (PANINI-SIGNIN owns these routes).
const SIGNIN_ROUTE =
  /^\/(signin$|auth\.js$|auth-style\.css$|learning\.(js|css)$|vendor\/webauthn\.js$|api\/(auth|enroll|privacy|profile|learning)\/)/;

function wantsDocument(req) {
  const destination = req.headers['sec-fetch-dest'];
  return destination
    ? destination === 'document'
    : (req.headers.accept || '').includes('text/html');
}

function safeNext(value) {
  return typeof value === 'string' && /^\/(?![/\\])[^\s]*$/.test(value) ? value : '/';
}

export function createGateway({
  backendUrl,
  searchUrl,
  searchFetch,
  searchClientRoot = join(ROOT, 'panini-core/capabilities/search/dist/client'),
  publicRoot = join(ROOT, 'frontend/public'),
  searchConfigured = false,
  production = false,
  allowedOrigins = [],
  rateLimit = 60,
  searchLimit = 4,
  // createSigninGate(...) from signin-gate.mjs: the face-and-voice gate. null disables it (unit tests only).
  signin = null,
  // Per-launch service token; the backend refuses console routes without it.
  consoleToken = '',
}) {
  const app = express();
  app.disable('x-powered-by');
  const limiter = createRateLimiter(rateLimit);
  const searches = createStreamLimiter(searchLimit);
  const consoleHeaders = consoleToken ? { 'x-panini-console': consoleToken } : {};
  app.use(secureRequests(allowedOrigins));
  app.use(express.json({ limit: '15mb' }));

  // MCP uses its own client credentials, with no browser Origin or console service token.
  app.all('/mcp', limitRequests(limiter), (req, res) => {
    if (req.path !== '/mcp') return res.sendStatus(404);
    if (!['GET', 'POST', 'DELETE'].includes(req.method)) return res.sendStatus(405);
    const host = req.headers.host || '';
    const peer = req.socket.remoteAddress || '';
    if (
      req.headers.origin !== undefined ||
      !/^(?:localhost|127\.0\.0\.1|\[::1\])(?::\d+)?$/i.test(host) ||
      !['127.0.0.1', '::1', '::ffff:127.0.0.1'].includes(peer)
    )
      return res.status(403).json({ error: 'MCP requires loopback access without Origin.' });
    return proxy(req, res, `${backendUrl}/mcp`, null, null);
  });

  // The PANINI shell API authenticates every call itself.
  app.use('/api/panini', (req, res) =>
    proxy(
      req,
      res,
      `${backendUrl}${req.originalUrl}`,
      null,
      req.path.endsWith('/events') ? null : req.path.endsWith('/stt/transcribe') ? 95000 : 65000,
    ),
  );

  if (signin) {
    // PANINI-SIGNIN owns sign-in: its page and APIs are relayed unchanged.
    app.use((req, res, next) => {
      if (!SIGNIN_ROUTE.test(req.path)) return next();
      return forwardToSignin(signin, req, res, req.originalUrl.replace(/^\/signin(?=\?|$)/, '/'));
    });
    // Shell tickets only lead to the sign-in page; they never open the console on their own.
    app.get('/auth/login', (req, res) =>
      res.redirect(303, `/signin?next=${encodeURIComponent(safeNext(req.query.next))}`),
    );
    app.post('/auth/logout', async (req, res) => {
      for (const cookie of await signin.lock(req.headers.cookie || '')) res.append('Set-Cookie', cookie);
      res.json({ status: 'signed_out' });
    });
    app.use(async (req, res, next) => {
      if (
        OPEN_PAGES.has(req.path) ||
        OPEN_API.has(req.path) ||
        (await signin.unlocked(req.headers.cookie || ''))
      )
        return next();
      const api =
        req.path.startsWith('/api/') ||
        req.path.startsWith('/search/api/') ||
        (req.path.startsWith('/search') && !['GET', 'HEAD'].includes(req.method));
      if (api)
        return res.status(401).json({
          error: 'Sign in to PANINI with your face and voice.',
          code: 'CONSOLE_SIGN_IN_REQUIRED',
        });
      if (req.method === 'GET' && wantsDocument(req))
        return res.redirect(302, `/signin?next=${encodeURIComponent(req.originalUrl)}`);
      return next(); // Static assets carry no user data.
    });
  }

  app.use(limitRequests(limiter));

  app.get('/api/health', async (_req, res) => {
    const check = async (url) => {
      try {
        const r = await fetch(url, { signal: AbortSignal.timeout(3000) });
        return r.ok;
      } catch {
        return false;
      }
    };
    const backendHealth = async () => {
      try {
        const response = await fetch(`${backendUrl}/api/health`, {
          signal: AbortSignal.timeout(3000),
        });
        return response.ok ? await response.json() : null;
      } catch {
        return null;
      }
    };
    const [details, search] = await Promise.all([
      backendHealth(),
      searchFetch ? true : check(`${searchUrl}/search/`),
    ]);
    const backend = details?.status === 'ok';
    res.status(backend && search ? 200 : 503).json({
      status: backend && search ? 'ok' : 'degraded',
      services: { backend, search },
      work: { ...details?.work, searches: { active: searches.active, limit: searchLimit } },
      providers: { ...details?.providers, search_configured: searchConfigured },
    });
  });
  app.use((req, res, next) => {
    const routed =
      BACKEND_ROUTES.has(`${req.method} ${req.path}`) ||
      BACKEND_PREFIXES.some(
        ([method, prefix]) => req.method === method && req.path.startsWith(prefix),
      );
    if (!routed) return next();
    return proxy(
      req,
      res,
      `${backendUrl}${req.originalUrl}`,
      null,
      req.path === '/api/voice/events' || req.path.startsWith('/api/agui/')
        ? null
        : req.path.endsWith('run-stream')
          ? req.body?.run_policy === 'continuous'
            ? null
            : (Math.min(3600, Math.max(5, Number(req.body?.timeout_seconds) || 120)) + 10) * 1000
          : req.path.includes('transcribe')
            ? 95000
            : 65000,
      OPEN_API.has(req.path) ? {} : consoleHeaders,
    );
  });

  const redirects = {
    '/index.html': '/',
    '/browser-use.html': '/browser',
    '/browser-use': '/browser',
  };
  for (const [from, to] of Object.entries(redirects))
    app.get(from, (_req, res) => res.redirect(301, to));
  // PANINI Admin (a single-page app built into public/admin) is the console's home.
  // Until it is built, / keeps serving the Compare page, so nothing breaks.
  const adminIndex = join(publicRoot, 'admin', 'index.html');
  const home = (_req, res) =>
    res.sendFile(existsSync(adminIndex) ? adminIndex : join(publicRoot, 'index.html'));
  app.get('/', home);
  app.get('/playground/compare', (_req, res) => res.sendFile(join(publicRoot, 'index.html')));
  app.get(
    // Spaces pages are deep links too: /spaces and /spaces/<space>/pages/<page> must survive a reload.
    /^\/(inbox|conversations|runs|receipts|projects|memory|playground|audit|spaces)\/?$|^\/(build|capabilities|settings)\/[a-z-]+\/?$|^\/spaces\/[A-Za-z0-9_-]{1,80}\/pages\/[A-Za-z0-9_-]{1,80}\/?$/,
    home,
  );
  app.get('/browser', (_req, res) => res.sendFile(join(publicRoot, 'browser-use.html')));
  app.get('/activity', (_req, res) => res.sendFile(join(publicRoot, 'panini-activity.html')));
  app.get('/agent', (_req, res) => res.sendFile(join(publicRoot, 'panini-agent.html')));
  app.use(express.static(publicRoot, { index: false, extensions: false }));
  app.get('/search', (req, res, next) => {
    if (req.query.q)
      return res.redirect(
        302,
        `/search/results${req.originalUrl.slice(req.originalUrl.indexOf('?'))}`,
      );
    if (req.path === '/search' && !req.originalUrl.startsWith('/search/'))
      return res.redirect(302, '/search/');
    next();
  });
  if (production) {
    app.use(
      '/search/assets',
      express.static(join(searchClientRoot, 'assets'), {
        immutable: true,
        maxAge: '1y',
        index: false,
      }),
    );
    // Missing chunks must never return an SSR document or consume a search slot.
    app.use('/search/assets', (_req, res) =>
      res.status(404).json({ error: 'Asset not found. Reload the page.' }),
    );
    app.use('/search', express.static(searchClientRoot, { index: false }));
  }
  app.use('/search', searches.middleware, (req, res) =>
    proxy(req, res, `${searchUrl || 'http://127.0.0.1'}${req.originalUrl}`, searchFetch, 95000),
  );
  app.use((_req, res) => res.status(404).json({ error: 'Not found' }));
  app.use((error, _req, res, _next) =>
    res.status(error.status || 500).json({
      error: error.type === 'entity.too.large' ? 'Request is too large' : 'Invalid request',
    }),
  );

  const server = createServer(app);
  const closeComparison = attachComparisonSocket(server, {
    backendUrl,
    allowedOrigins,
    limiter,
    authorize: signin ? (req) => signin.unlocked(req.headers.cookie || '') : null,
    consoleToken,
  });
  // Registered after the comparison socket so it can 404 unclaimed paths.
  const closeExtension = attachExtensionSocket(server, { backendUrl });
  return {
    app,
    server,
    close: async () => {
      closeComparison();
      closeExtension();
      server.closeAllConnections();
      await new Promise((resolve) => server.close(resolve));
    },
  };
}
