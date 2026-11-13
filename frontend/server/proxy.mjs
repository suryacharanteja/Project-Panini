import { Readable } from 'node:stream';
import { pipeline } from 'node:stream/promises';
import { EXTENSION_ORIGIN, MAX_BODY_BYTES } from './security.mjs';

const HOP_HEADERS = new Set([
  'content-encoding',
  'content-length',
  'transfer-encoding',
  'connection',
  'keep-alive',
]);

async function pipeResponse(response, res) {
  res.statusCode = response.status;
  for (const [key, value] of response.headers) {
    if (!HOP_HEADERS.has(key)) res.setHeader(key, value);
  }
  if (!response.body) return res.end();
  await pipeline(Readable.fromWeb(response.body), res);
}

/** Preserve streaming/backpressure and tie upstream lifetime to the client. */
export async function proxy(req, res, target, handler, timeoutMs = 65000, extraHeaders = {}) {
  const disconnected = new AbortController();
  const onClose = () => {
    if (!res.writableFinished) disconnected.abort();
  };
  res.once('close', onClose);
  // Continuous browser work has no overall deadline. Header readiness stays
  // bounded, and client disconnect still aborts the backend stream.
  const headersDeadline = new AbortController();
  const headersTimer = timeoutMs === null ? setTimeout(() => headersDeadline.abort(), 65000) : null;
  const signal = AbortSignal.any([
    disconnected.signal,
    timeoutMs === null ? headersDeadline.signal : AbortSignal.timeout(timeoutMs),
  ]);
  const headers = new Headers();
  for (const name of ['content-type', 'accept'])
    if (req.headers[name]) headers.set(name, req.headers[name]);
  if (new URL(target).pathname.startsWith('/api/panini/')) {
    for (const name of ['authorization', 'x-panini-instance', 'x-panini-lease-epoch'])
      if (req.headers[name]) headers.set(name, req.headers[name]);
    const cookie = (req.headers.cookie || '')
      .split(';')
      .map((value) => value.trim())
      .filter((value) => /^panini_observer_[a-f0-9]+=/.test(value))
      .join('; ');
    if (cookie) headers.set('cookie', cookie);
  }
  const pathname = new URL(target).pathname;
  if (pathname === '/mcp') {
    for (const name of [
      'authorization',
      'mcp-session-id',
      'mcp-protocol-version',
      'mcp-method',
      'mcp-name',
      'last-event-id',
    ])
      if (req.headers[name]) headers.set(name, req.headers[name]);
    // Modern MCP mirrors explicitly annotated scalar parameters into these headers.
    for (const [name, value] of Object.entries(req.headers))
      if (/^mcp-param-[a-z0-9_-]+$/i.test(name) && value) headers.set(name, value);
  }
  // A2A callers authenticate with their own bearer token; the card needs the public host.
  if (pathname === '/a2a' && req.headers.authorization)
    headers.set('authorization', req.headers.authorization);
  if (
    (pathname === '/.well-known/agent-card.json' ||
      pathname.startsWith('/api/harness/connectors/')) &&
    req.headers.host
  )
    headers.set('x-forwarded-host', req.headers.host);
  // Pairing binds a device to the extension that redeemed the code, so keep its origin.
  const pairing =
    new URL(target).pathname === '/api/extension/pair' &&
    EXTENSION_ORIGIN.test(req.headers.origin || '');
  if (pathname !== '/mcp')
    headers.set('origin', pairing ? req.headers.origin : new URL(target).origin);
  headers.set('x-request-id', req.requestId);
  for (const [name, value] of Object.entries(extraHeaders)) headers.set(name, value);
  let tooLarge = false;
  let body;
  if (!['GET', 'HEAD'].includes(req.method)) {
    body = req.is('application/json')
      ? JSON.stringify(req.body)
      : Readable.from(
          (async function* () {
            let size = 0;
            // Do not destroy the incoming socket on early iterator exit; send a 413 first.
            for await (const chunk of req.iterator({ destroyOnReturn: false })) {
              size += chunk.length;
              if (size > MAX_BODY_BYTES) {
                tooLarge = true;
                throw new Error('Body limit exceeded');
              }
              yield chunk;
            }
          })(),
        );
  }
  try {
    const upstream = new Request(target, {
      method: req.method,
      headers,
      body,
      duplex: 'half',
      signal,
    });
    const response = handler ? await handler(upstream) : await fetch(upstream);
    clearTimeout(headersTimer);
    await pipeResponse(response, res);
  } catch {
    if (disconnected.signal.aborted) return;
    if (res.headersSent) return res.destroy();
    const status = tooLarge ? 413 : signal.aborted ? 504 : 502;
    const error = tooLarge
      ? 'Request is too large'
      : signal.aborted
        ? 'Upstream request timed out'
        : 'Service unavailable. Check service health.';
    res.status(status).json({ error, request_id: req.requestId });
    if (tooLarge) req.resume();
    // Do not log upstream error bodies, which may contain credentials or user content.
    console.error(JSON.stringify({ event: 'proxy.failure', request_id: req.requestId, status }));
  } finally {
    clearTimeout(headersTimer);
    res.removeListener('close', onClose);
  }
}
