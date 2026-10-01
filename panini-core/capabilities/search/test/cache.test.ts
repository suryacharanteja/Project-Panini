import { describe, expect, it, vi } from 'vitest';
import { cacheKey, cachedSearch, cacheTtl, memoryCache } from '@/lib/cache';

describe('cachedSearch', () => {
  it('bypasses v1 entries and preserves publication dates on cache hits', async () => {
    const cache = memoryCache();
    await cache.put(
      'v1|google|x|any|||8',
      JSON.stringify([{ title: 'old', link: 'https://a.com', snippet: '' }]),
    );
    const results = [
      { title: 'new', link: 'https://a.com', snippet: '', published_date: '2026-09-18T10:00:00Z' },
    ];
    const run = vi.fn(async () => results);
    expect(await cachedSearch(cache, { query: 'x' }, run)).toEqual({ results, cached: false });
    expect(await cachedSearch(cache, { query: 'x' }, run)).toEqual({ results, cached: true });
    expect(run).toHaveBeenCalledOnce();
  });

  it('runs once, then serves the same params from the cache with a window-based ttl', async () => {
    const cache = memoryCache();
    const run = vi.fn(async () => [{ title: 't', link: 'https://a.com', snippet: 's' }]);
    const params = { query: 'Bun 1.3', service: 'web', timeRange: 'week' as const };
    const first = await cachedSearch(cache, params, run);
    const second = await cachedSearch(cache, { ...params, query: ' bun 1.3 ' }, run);
    expect(first.cached).toBe(false);
    expect(second.cached).toBe(true);
    expect(run).toHaveBeenCalledTimes(1);
    expect(cacheTtl(params)).toBe(3600);
    expect(cacheKey(params)).not.toBe(cacheKey({ ...params, timeRange: 'day' }));
  });
  it('waits for cache writes and keeps results when storage fails', async () => {
    const results = [{ title: 't', link: 'https://a.com', snippet: 's' }];
    let finish!: () => void;
    const put = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          finish = resolve;
        }),
    );
    let settled = false;
    const response = cachedSearch(
      { get: async () => null, put },
      { query: 'x' },
      async () => results,
    ).then((value) => {
      settled = true;
      return value;
    });
    await vi.waitFor(() => expect(put).toHaveBeenCalledOnce());
    expect(settled).toBe(false);
    finish();
    expect((await response).results).toEqual(results);
    const broken = {
      get: async () => {
        throw new Error('Unavailable');
      },
      put: async () => {
        throw new Error('Unavailable');
      },
    };
    expect((await cachedSearch(broken, { query: 'x' }, async () => results)).results).toEqual(
      results,
    );
  });
  it('does not cache empty results', async () => {
    const cache = memoryCache();
    const run = vi.fn(async () => []);
    await cachedSearch(cache, { query: 'x', timeRange: undefined }, run);
    await cachedSearch(cache, { query: 'x', timeRange: undefined }, run);
    expect(run).toHaveBeenCalledTimes(2);
  });
});
