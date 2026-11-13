/**
 * Face-and-voice gate for the PANINI console.
 *
 * PANINI-SIGNIN (a supervised child process) owns sign-in: it challenges the
 * person to move their head and read digits, checks face, voice and liveness,
 * and then sets its own HttpOnly session cookie. This module asks that service
 * whether the caller's cookie is unlocked. Anything that cannot be verified
 * is treated as locked (fail closed).
 */

const SIGNIN_JSON = 'application/json';

export function createSigninGate({
  url = '',
  ttlMs = 2000,
  timeoutMs = 3000,
  now = Date.now,
  fetchImpl = fetch,
} = {}) {
  const cache = new Map();
  const base = url ? url.replace(/\/$/, '') : '';

  return {
    url: base,
    /** True only when the sign-in service reports an unlocked session for this cookie. */
    async unlocked(cookie = '') {
      if (!base || !cookie) return false;
      const hit = cache.get(cookie);
      if (hit && hit.until > now()) return hit.value;
      let value = false;
      try {
        const response = await fetchImpl(`${base}/api/auth/status`, {
          headers: { cookie },
          signal: AbortSignal.timeout(timeoutMs),
        });
        value = response.ok && (await response.json()).unlocked === true;
      } catch {
        value = false;
      }
      for (const [key, entry] of cache) if (entry.until <= now()) cache.delete(key);
      cache.set(cookie, { value, until: now() + ttlMs });
      return value;
    },
    /** Locks the session behind this cookie on the sign-in service. */
    async lock(cookie = '') {
      if (!base) return [];
      cache.delete(cookie);
      try {
        const response = await fetchImpl(`${base}/api/auth/lock`, {
          method: 'POST',
          headers: { cookie, 'content-type': SIGNIN_JSON },
          body: '{}',
          signal: AbortSignal.timeout(timeoutMs),
        });
        return response.headers.getSetCookie?.() ?? [];
      } catch {
        return [];
      }
    },
    fetchImpl,
    timeoutMs,
  };
}

/**
 * Relays one request to the sign-in service. Only the headers sign-in needs
 * travel (cookie, origin, content type); its Set-Cookie headers come back so
 * the browser receives the session.
 */
export async function forwardToSignin(gate, req, res, path) {
  if (!gate.url) {
    return res.status(503).json({
      error: 'PANINI sign-in is not configured. Set PANINI_SIGNIN_DIR and restart PANINI.',
      code: 'SIGNIN_UNAVAILABLE',
    });
  }
  const headers = { accept: req.headers.accept || '*/*' };
  for (const name of ['cookie', 'origin']) if (req.headers[name]) headers[name] = req.headers[name];
  const json = (req.headers['content-type'] || '').startsWith(SIGNIN_JSON);
  if (json) headers['content-type'] = SIGNIN_JSON;
  const hasBody = !['GET', 'HEAD'].includes(req.method);
  try {
    const response = await gate.fetchImpl(`${gate.url}${path}`, {
      method: req.method,
      headers,
      // express.json has already parsed the body; send it back as JSON.
      body: hasBody && json ? JSON.stringify(req.body ?? {}) : undefined,
      redirect: 'manual',
      signal: AbortSignal.timeout(65000),
    });
    res.status(response.status);
    for (const name of ['content-type', 'cache-control', 'location'])
      if (response.headers.get(name)) res.setHeader(name, response.headers.get(name));
    for (const cookie of response.headers.getSetCookie?.() ?? []) res.append('Set-Cookie', cookie);
    res.end(Buffer.from(await response.arrayBuffer()));
  } catch {
    res.status(502).json({
      error: 'PANINI sign-in is not reachable. Restart PANINI to bring it back.',
      code: 'SIGNIN_UNAVAILABLE',
    });
  }
}
