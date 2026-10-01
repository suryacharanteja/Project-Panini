// FRONTEND (gateway and web console), BACKEND and SEARCH; the PANINI shell uses the same values.
export const DEFAULT_PORTS = [3200, 8200, 3230];

export function configured(name, env = process.env) {
  const value = env[name]?.trim();
  return Boolean(value && !value.startsWith('your_') && value !== 'change-me');
}

export function parseOrigins(value = '') {
  return value
    .split(',')
    .map((origin) => origin.trim())
    .filter(Boolean)
    .map((origin) => {
      let url;
      try {
        url = new URL(origin);
      } catch {
        throw new Error('ALLOWED_ORIGINS contains an invalid origin');
      }
      if (
        !['http:', 'https:'].includes(url.protocol) ||
        url.username ||
        url.password ||
        url.pathname !== '/' ||
        url.search ||
        url.hash
      ) {
        throw new Error(
          'ALLOWED_ORIGINS must contain HTTP(S) origins without paths or credentials',
        );
      }
      return url.origin;
    });
}

export function readConfig(env = process.env, production = false) {
  const ports = ['FRONTEND_PORT', 'BACKEND_PORT', 'SEARCH_PORT'].map((name, index) => {
    const value = Number(env[name] || DEFAULT_PORTS[index]);
    if (!Number.isInteger(value) || value < 1 || value > 65535)
      throw new Error(`${name} must be an integer from 1 to 65535`);
    return value;
  });
  const used = production ? ports.slice(0, 2) : ports;
  if (new Set(used).size !== used.length) throw new Error('Service ports must be distinct');
  return {
    port: ports[0],
    backendPort: ports[1],
    searchPort: ports[2],
    allowedOrigins: parseOrigins(env.ALLOWED_ORIGINS),
    searchConfigured: configured('OPENAI_API_KEY', env) && configured('AI_GATEWAY_API_KEY', env),
  };
}
