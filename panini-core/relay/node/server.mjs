// PANINI Relay, Node reference implementation (V2-14 §5.9). The relay is blind: it pairs one PC socket with up to
// 64 device sockets per channel and forwards frames it cannot read (the fabric envelopes are end-to-end encrypted).
// It stores only channel records and the phone app's static assets. Same protocol as the Cloudflare Worker.
//
//   POST /channels                 -> {channel_id, pc_secret}
//   GET  /pc/:channel   (WS)       PC: HMAC challenge with pc_secret; one PC socket per channel
//   GET  /d/:channel    (WS)       devices: frames forwarded to the PC as {conn, data}, replies routed back
//   ANY  /hook/:channel/:hook/:secret   webhook deliveries forwarded to the PC; the PC's answer is returned
//   GET  /app/*                    static phone-app assets and signed manifest
//
// Run: node panini-core/relay/node/server.mjs [port]   (RELAY_LOG=path records frames for the I6 test)
import { createHmac, randomBytes, randomUUID, timingSafeEqual } from 'node:crypto';
import { appendFileSync, existsSync, readFileSync } from 'node:fs';
import { createServer } from 'node:http';
import { extname, join, normalize, resolve } from 'node:path';
import { WebSocketServer } from 'ws';

const MAX_DEVICES = 64;
const MAX_FRAME = 1024 * 1024;
const FRAMES_PER_SECOND = 60;
const HOOK_TIMEOUT_MS = 15000;
const APP_ROOT = resolve(process.env.RELAY_APP_ROOT || join(import.meta.dirname, 'app'));
const channels = new Map(); // id -> {secret, pc, devices: Map(conn -> ws), hooks: Map(id -> resolve)}

const log = (entry) => {
  if (process.env.RELAY_LOG) appendFileSync(process.env.RELAY_LOG, JSON.stringify(entry) + '\n');
};

function limited(ws) {
  const now = Date.now();
  ws.window = (ws.window || []).filter((t) => now - t < 1000);
  ws.window.push(now);
  return ws.window.length > FRAMES_PER_SECOND;
}

const server = createServer(async (req, res) => {
  const url = new URL(req.url, 'http://relay');
  const parts = url.pathname.split('/').filter(Boolean);
  if (req.method === 'POST' && url.pathname === '/channels') {
    const id = randomBytes(16).toString('hex');
    const secret = randomBytes(32).toString('base64url');
    channels.set(id, { secret, pc: null, devices: new Map(), hooks: new Map() });
    res.writeHead(200, { 'content-type': 'application/json' });
    return res.end(JSON.stringify({ channel_id: id, pc_secret: secret }));
  }
  if (parts[0] === 'hook' && parts.length >= 4) {
    const channel = channels.get(parts[1]);
    if (!channel?.pc) {
      res.writeHead(503);
      return res.end('PANINI is offline.');
    }
    const chunks = [];
    for await (const chunk of req) chunks.push(chunk);
    const body = Buffer.concat(chunks);
    if (body.length > MAX_FRAME) {
      res.writeHead(413);
      return res.end();
    }
    const id = randomUUID();
    const allowed = Object.fromEntries(
      Object.entries(req.headers).filter(([k]) => /^(content-type|webhook-|x-hub-signature|x-panini-signature|x-twilio)/i.test(k)),
    );
    const reply = new Promise((done) => {
      channel.hooks.set(id, done);
      setTimeout(() => channel.hooks.delete(id) && done({ status: 504, body: '' }), HOOK_TIMEOUT_MS);
    });
    channel.pc.send(
      JSON.stringify({
        hook: { id, method: req.method, path: '/' + parts.slice(2).join('/'), query: url.search, headers: allowed,
          body: body.toString('base64'), received_at: Date.now() / 1000 },
      }),
    );
    const answer = await reply;
    res.writeHead(answer.status || 200, { 'content-type': answer.content_type || 'text/plain' });
    return res.end(answer.body || '');
  }
  if (req.method === 'GET' && parts[0] === 'app') {
    const file = normalize(join(APP_ROOT, ...parts.slice(1)));
    if (!file.startsWith(APP_ROOT) || !existsSync(file)) {
      res.writeHead(404);
      return res.end();
    }
    const types = { '.html': 'text/html', '.js': 'text/javascript', '.mjs': 'text/javascript', '.json': 'application/json', '.css': 'text/css' };
    res.writeHead(200, { 'content-type': types[extname(file)] || 'application/octet-stream' });
    return res.end(readFileSync(file));
  }
  res.writeHead(404);
  res.end();
});

const wss = new WebSocketServer({ noServer: true, maxPayload: MAX_FRAME });

server.on('upgrade', (req, socket, head) => {
  const [role, id] = new URL(req.url, 'http://relay').pathname.split('/').filter(Boolean);
  const channel = channels.get(id);
  if (!channel || !['pc', 'd'].includes(role)) return socket.destroy();
  if (role === 'd' && channel.devices.size >= MAX_DEVICES) return socket.destroy();
  wss.handleUpgrade(req, socket, head, (ws) => (role === 'pc' ? attachPc(channel, ws) : attachDevice(channel, ws)));
});

function attachPc(channel, ws) {
  const challenge = randomBytes(32).toString('base64url');
  let authed = false;
  ws.send(JSON.stringify({ challenge }));
  ws.on('message', (raw) => {
    if (limited(ws)) return;
    let msg;
    try {
      msg = JSON.parse(raw);
    } catch {
      return ws.close(1003);
    }
    if (!authed) {
      const want = createHmac('sha256', channel.secret).update(challenge).digest();
      const got = Buffer.from(String(msg.hmac || ''), 'base64url');
      if (got.length !== want.length || !timingSafeEqual(got, want)) return ws.close(4001, 'bad channel secret');
      authed = true;
      channel.pc?.close(4000, 'replaced');
      channel.pc = ws;
      return ws.send(JSON.stringify({ ok: true }));
    }
    if (msg.hook_reply) {
      const done = channel.hooks.get(msg.hook_reply.id);
      channel.hooks.delete(msg.hook_reply.id);
      return done?.(msg.hook_reply);
    }
    log({ to: 'device', conn: msg.conn, data: msg.data });
    channel.devices.get(msg.conn)?.send(JSON.stringify(msg.data));
  });
  ws.on('close', () => {
    if (channel.pc === ws) channel.pc = null;
  });
}

function attachDevice(channel, ws) {
  const conn = randomUUID();
  channel.devices.set(conn, ws);
  ws.on('message', (raw) => {
    if (limited(ws) || !channel.pc) return;
    let data;
    try {
      data = JSON.parse(raw);
    } catch {
      return ws.close(1003);
    }
    log({ to: 'pc', conn, data });
    channel.pc.send(JSON.stringify({ conn, data }));
  });
  ws.on('close', () => {
    channel.devices.delete(conn);
    channel.pc?.send(JSON.stringify({ conn, closed: true }));
  });
}

const port = Number(process.argv[2] || process.env.PORT || 8790);
server.listen(port, '127.0.0.1', () => console.log(`relay listening on ${server.address().port}`));
