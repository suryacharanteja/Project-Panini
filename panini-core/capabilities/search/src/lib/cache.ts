import type { RawResult, SearchParams } from './web-search';

/** Small cache port shared by the pipeline, memory adapter and test doubles. */
export interface ResultCache {
  get(key: string): Promise<string | null>;
  put(key: string, value: string, options?: { expirationTtl?: number }): Promise<void>;
}

/** How long a lane's rows stay fresh, by how recent the user wants them. */
const TTL_SECONDS: Record<string, number> = {
  day: 10 * 60,
  week: 60 * 60,
  month: 2 * 60 * 60,
  any: 6 * 60 * 60,
};

export function cacheKey(params: SearchParams): string {
  return [
    'v2', // v1 discarded published_date before caching results.
    params.service ?? 'web',
    params.query.trim().toLowerCase(),
    params.timeRange ?? 'any',
    (params.includeSites ?? []).join(','),
    (params.excludeSites ?? []).join(','),
    params.maxResults ?? 8,
  ].join('|');
}

export function cacheTtl(params: SearchParams): number {
  return TTL_SECONDS[params.timeRange ?? 'any'] ?? TTL_SECONDS.any!;
}

export async function cachedSearch(
  cache: ResultCache | undefined,
  params: SearchParams,
  run: () => Promise<RawResult[]>,
): Promise<{ results: RawResult[]; cached: boolean }> {
  if (!cache) return { results: await run(), cached: false };
  const key = cacheKey(params);
  try {
    const hit = await cache.get(key);
    if (hit) return { results: JSON.parse(hit) as RawResult[], cached: true };
  } catch {
    // A broken cache must never break a search.
  }
  const results = await run();
  if (results.length > 0) {
    await cache
      .put(key, JSON.stringify(results), { expirationTtl: cacheTtl(params) })
      .catch(() => undefined);
  }
  return { results, cached: false };
}

/** Bounded process-local cache; TTLs also apply in the Node runtime. */
export function memoryCache(maxEntries = 500, now = Date.now): ResultCache & { size: number } {
  const map = new Map<string, { value: string; expires: number }>();
  return {
    get: async (k) => {
      const item = map.get(k);
      if (!item) return null;
      if (item.expires <= now()) {
        map.delete(k);
        return null;
      }
      return item.value;
    },
    put: async (k, v, options) => {
      map.delete(k);
      map.set(k, { value: v, expires: now() + (options?.expirationTtl ?? 600) * 1000 });
      if (map.size > maxEntries) map.delete(map.keys().next().value!);
    },
    get size() {
      return map.size;
    },
  };
}
