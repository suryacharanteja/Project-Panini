/**
 * Web-console sign-in: one-time signed tickets become an HttpOnly session cookie.
 *
 * The PANINI shell (or the runtime's console link) mints a ticket with the
 * per-launch console secret. Redeeming it sets a SameSite=Strict cookie, so a
 * website cannot ride the session and a local process cannot use the console
 * without a ticket. Tickets are single use and short lived.
 */
import { createHmac, randomBytes, timingSafeEqual } from 'node:crypto';

export const COOKIE = 'panini_console';
const HOUR = 60 * 60 * 1000;

export function signTicket(secret, expires, nonce) {
  const payload = `v1.${expires}.${nonce}`;
  return `${payload}.${createHmac('sha256', secret).update(payload).digest('base64url')}`;
}

export function mintTicket(secret, { now = Date.now(), ttl = 10 * 60 * 1000 } = {}) {
  return signTicket(secret, now + ttl, randomBytes(16).toString('base64url'));
}

function cookies(header = '') {
  return Object.fromEntries(
    header
      .split(';')
      .map((part) => part.trim().split('='))
      .filter(([name, value]) => name && value)
      .map(([name, ...value]) => [name, value.join('=')]),
  );
}

export function createConsoleAuth({
  secret,
  now = Date.now,
  sessionTtl = 12 * HOUR,
  maxSessions = 16,
}) {
  if (!secret || secret.length < 32)
    throw new Error('A console secret of at least 32 characters is required');
  const sessions = new Map();
  const redeemed = new Map();
  const prune = () => {
    const time = now();
    for (const [key, expires] of sessions) if (expires <= time) sessions.delete(key);
    for (const [key, expires] of redeemed) if (expires <= time) redeemed.delete(key);
  };
  return {
    ticket: (ttl) => mintTicket(secret, { now: now(), ttl }),
    /** Returns a new session id, or null for an invalid, expired or reused ticket. */
    redeem(ticket) {
      prune();
      const match = /^v1\.(\d{1,16})\.([A-Za-z0-9_-]{16,64})\.([A-Za-z0-9_-]{43})$/.exec(
        String(ticket || ''),
      );
      if (!match) return null;
      const [, expires, nonce, mac] = match;
      const expected = Buffer.from(signTicket(secret, expires, nonce).split('.').pop());
      const supplied = Buffer.from(mac);
      if (supplied.length !== expected.length || !timingSafeEqual(supplied, expected)) return null;
      if (Number(expires) <= now() || redeemed.has(nonce)) return null;
      redeemed.set(nonce, Number(expires));
      if (sessions.size >= maxSessions) sessions.delete(sessions.keys().next().value);
      const id = randomBytes(32).toString('base64url');
      sessions.set(id, now() + sessionTtl);
      return id;
    },
    authenticated(req) {
      const id = cookies(req.headers.cookie)[COOKIE];
      const expires = id && sessions.get(id);
      if (!expires || expires <= now()) return false;
      sessions.set(id, now() + sessionTtl); // Sliding expiry while the console is in use.
      return true;
    },
    signOut(req) {
      sessions.delete(cookies(req.headers.cookie)[COOKIE]);
    },
    cookie: (id) =>
      `${COOKIE}=${id}; HttpOnly; SameSite=Strict; Path=/; Max-Age=${Math.floor(sessionTtl / 1000)}`,
    expiredCookie: `${COOKIE}=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0`,
  };
}
