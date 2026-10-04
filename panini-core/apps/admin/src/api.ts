import { useCallback, useEffect, useRef, useState } from 'react';

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

/** Every call goes through the signed-in console session (HttpOnly cookie). */
export async function api<T = any>(
  path: string,
  options: { method?: string; body?: unknown } = {},
): Promise<T> {
  const response = await fetch(path, {
    method: options.method ?? (options.body !== undefined ? 'POST' : 'GET'),
    credentials: 'same-origin',
    headers: options.body !== undefined ? { 'Content-Type': 'application/json' } : {},
    body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail =
      data?.error?.message ??
      data?.detail?.message ??
      (typeof data?.detail === 'string' ? data.detail : data?.detail?.[0]?.msg) ??
      `Request failed (${response.status}).`;
    throw new ApiError(
      response.status === 401 ? 'Your console session ended. Sign in again from PANINI.' : detail,
      response.status,
    );
  }
  return data as T;
}

export const admin = (path: string) => `/api/admin/v1${path}`;
export const harness = (path: string) => `/api/harness${path}`;

/** Load a resource, refresh on an interval while visible, and expose reload(). */
export function useResource<T>(path: string | null, intervalMs = 0) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string>('');
  const [loading, setLoading] = useState(true);
  const alive = useRef(true);
  const request = useRef(0);

  const reload = useCallback(async () => {
    if (!path) return;
    const current = ++request.current;
    try {
      const value = await api<T>(path);
      if (alive.current && current === request.current) {
        setData(value);
        setError('');
      }
    } catch (err) {
      if (alive.current && current === request.current) setError((err as Error).message);
    } finally {
      if (alive.current && current === request.current) setLoading(false);
    }
  }, [path]);

  useEffect(() => {
    alive.current = true;
    request.current++;
    setData(null);
    setError('');
    setLoading(true);
    void reload();
    if (!intervalMs)
      return () => {
        alive.current = false;
        request.current++;
      };
    const timer = setInterval(() => {
      if (!document.hidden) void reload();
    }, intervalMs);
    return () => {
      alive.current = false;
      request.current++;
      clearInterval(timer);
    };
  }, [reload, intervalMs]);

  return { data, error, loading, reload, setData };
}

export function when(seconds?: number | null) {
  if (!seconds) return '';
  return new Date(seconds * 1000).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' });
}

export function ago(seconds?: number | null) {
  if (!seconds) return '';
  const delta = Date.now() / 1000 - seconds;
  if (delta < 60) return 'just now';
  if (delta < 3600) return `${Math.round(delta / 60)} min ago`;
  if (delta < 86400) return `${Math.round(delta / 3600)} h ago`;
  return when(seconds);
}
