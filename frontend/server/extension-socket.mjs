import { WebSocketServer, WebSocket } from 'ws';
import { allowedRequest, EXTENSION_ORIGIN } from './security.mjs';

const PATH = '/ws/extension';
/** Paths some other upgrade handler owns; anything else is a 404. */
const KNOWN_PATHS = new Set(['/ws/compare', PATH]);

/**
 * Relays the browser extension to the backend on the runtime's one public port.
 *
 * Web pages are refused here by origin. Authentication is end to end: the
 * backend admits only a paired device that completes the mutual challenge,
 * and the extension obeys only a server that proves the same pairing. The
 * extension origin is forwarded so the backend can bind it to the pairing.
 */
export function attachExtensionSocket(server, { backendUrl }) {
  const wss = new WebSocketServer({ noServer: true, maxPayload: 4_000_000 });

  server.on('upgrade', (req, socket, head) => {
    if (req.url !== PATH) {
      // Registered last, so by here nobody claimed an unknown path.
      if (!KNOWN_PATHS.has(req.url ?? '')) {
        socket.end('HTTP/1.1 404 Not Found\r\nConnection: close\r\n\r\n');
      }
      return;
    }
    const origin = req.headers.origin ?? '';
    if (
      !allowedRequest({ headers: { host: req.headers.host } }) ||
      (origin && !EXTENSION_ORIGIN.test(origin))
    ) {
      socket.end('HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n');
      return;
    }
    if (wss.clients.size >= 2) {
      socket.end('HTTP/1.1 503 Service Unavailable\r\nConnection: close\r\n\r\n');
      return;
    }
    wss.handleUpgrade(req, socket, head, (ws) => wss.emit('connection', ws, req));
  });

  wss.on('connection', (client, req) => {
    const target = backendUrl.replace(/^http/, 'ws') + PATH;
    const origin = req.headers.origin;
    const upstream = new WebSocket(target, {
      maxPayload: 4_000_000,
      handshakeTimeout: 5000,
      ...(origin ? { origin } : {}),
    });
    const queued = [];

    const flush = () => {
      while (queued.length && upstream.readyState === WebSocket.OPEN) {
        upstream.send(queued.shift());
      }
    };

    upstream.on('open', flush);
    // Frames can arrive before the backend socket finishes opening; holding
    // them means the extension's "hello" is never silently dropped.
    client.on('message', (data) => {
      const frame = data.toString();
      if (upstream.readyState === WebSocket.OPEN) upstream.send(frame);
      else if (upstream.readyState === WebSocket.CONNECTING) {
        if (queued.length >= 8 || frame.length > 65536)
          return client.close(1009, 'Too much pending data');
        queued.push(frame);
      }
    });
    upstream.on('message', (data) => {
      if (client.readyState === WebSocket.OPEN) client.send(data.toString());
    });

    const shutdown = (code = 1011, reason = 'Extension connection ended') => {
      // Reserved close codes cannot be sent on the wire. Preserve policy
      // rejection reasons so the popup can explain and stop reconnecting.
      if (typeof code !== 'number' || [1005, 1006, 1015].includes(code)) code = 1011;
      const message = typeof reason === 'string' ? reason : reason.toString();
      queued.length = 0;
      if (client.readyState === WebSocket.OPEN) client.close(code, message);
      if (upstream.readyState === WebSocket.OPEN || upstream.readyState === WebSocket.CONNECTING) {
        upstream.close(code, message);
      }
    };
    client.on('close', shutdown);
    client.on('error', shutdown);
    upstream.on('close', shutdown);
    upstream.on('error', (error) => {
      console.error('[EXTENSION] Backend socket error:', error.message);
      shutdown();
    });
  });

  return () => {
    for (const client of wss.clients) client.terminate();
    wss.close();
  };
}
