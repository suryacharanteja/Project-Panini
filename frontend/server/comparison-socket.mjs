import { randomUUID } from 'node:crypto';
import { WebSocketServer, WebSocket } from 'ws';
import { allowedRequest } from './security.mjs';

export function attachComparisonSocket(
  server,
  {
    backendUrl,
    allowedOrigins,
    limiter,
    consoleAuth = null,
    consoleToken = '',
    maxConnections = 32,
  },
) {
  const wss = new WebSocketServer({ noServer: true, maxPayload: 100000 });
  server.on('upgrade', (req, socket, head) => {
    // Own only this path. Other upgrade handlers are registered on the same
    // server, so closing sockets we do not own would kill theirs.
    if (req.url !== '/ws/compare') return;
    if (!allowedRequest(req, allowedOrigins) || (consoleAuth && !consoleAuth.authenticated(req))) {
      socket.end('HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n');
      return;
    }
    if (wss.clients.size >= maxConnections) {
      socket.end('HTTP/1.1 503 Service Unavailable\r\nConnection: close\r\n\r\n');
      return;
    }
    wss.handleUpgrade(req, socket, head, (ws) => wss.emit('connection', ws));
  });
  wss.on('connection', (ws) => {
    let active = null;
    ws.alive = true;
    const send = (data) => {
      if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(data));
    };
    ws.on('pong', () => {
      ws.alive = true;
    });
    ws.on('close', () => active?.abort());
    ws.on('error', () => active?.abort());
    ws.on('message', async (bytes) => {
      let id;
      let controller;
      try {
        const payload = JSON.parse(bytes.toString());
        if (!payload || typeof payload !== 'object') throw new Error('Invalid request');
        id = typeof payload.id === 'string' ? payload.id : undefined;
        if (!id || id.length > 100)
          return send({ id, error: 'A request ID of 1–100 characters is required' });
        if (active) return send({ id, error: 'A comparison is already running' });
        if (!limiter.take())
          return send({ id, error: 'Too many requests. Try again shortly.', httpStatus: 429 });
        controller = new AbortController();
        active = controller;
        const response = await fetch(`${backendUrl}/api/compare`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-Request-ID': randomUUID(),
            ...(consoleToken ? { 'x-panini-console': consoleToken } : {}),
          },
          body: JSON.stringify({ text: payload.text, task: payload.task }),
          signal: AbortSignal.any([controller.signal, AbortSignal.timeout(65000)]),
        });
        const result = await response.json();
        send(
          response.ok
            ? { ...result, id }
            : {
                id,
                error:
                  typeof result.detail === 'string' ? result.detail : 'Invalid comparison request',
                httpStatus: response.status,
              },
        );
      } catch (error) {
        send({
          id,
          error: error.name === 'SyntaxError' ? 'Invalid JSON' : 'Comparison failed or timed out',
        });
      } finally {
        if (controller && active === controller) active = null;
      }
    });
  });
  const heartbeat = setInterval(() => {
    for (const ws of wss.clients) {
      if (!ws.alive) {
        ws.terminate();
        continue;
      }
      ws.alive = false;
      ws.ping();
    }
  }, 30000).unref();
  return () => {
    clearInterval(heartbeat);
    for (const ws of wss.clients) ws.terminate();
    wss.close();
  };
}
