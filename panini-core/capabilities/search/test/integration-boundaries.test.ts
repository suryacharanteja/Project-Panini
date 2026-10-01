import { describe, expect, it, vi } from 'vitest';
import { memoryCache } from '@/lib/cache';
import { validateAskRequest } from '@/lib/validate';
import { decodeEntities, search } from '@/lib/web-search';
import { canonicalUrl } from '@/lib/rank';

describe('search boundaries', () => {
  it('does not merge case-sensitive paths, ports, or query values with separators', () => {
    expect(canonicalUrl('https://example.com/ReadMe')).not.toBe(
      canonicalUrl('https://example.com/readme'),
    );
    expect(canonicalUrl('https://example.com:8443/doc')).not.toBe(
      canonicalUrl('https://example.com/doc'),
    );
    expect(canonicalUrl('https://example.com/?a=x%26b%3Dy')).not.toBe(
      canonicalUrl('https://example.com/?a=x&b=y'),
    );
  });
  it('deduplicates source filters so no engine lane is lost or billed twice', () => {
    expect(validateAskRequest({ q: 'test', s: ['web', 'web', 'reddit'] }).s).toEqual([
      'web',
      'reddit',
    ]);
  });

  it('expires cached results and bounds memory', async () => {
    let now = 0;
    const cache = memoryCache(2, () => now);
    await cache.put('a', 'one', { expirationTtl: 1 });
    now = 1000;
    expect(await cache.get('a')).toBeNull();
    await cache.put('a', 'one');
    await cache.put('b', 'two');
    await cache.put('c', 'three');
    expect(cache.size).toBe(2);
    expect(await cache.get('a')).toBeNull();
  });

  it('ignores unsafe result URLs and malformed snippets', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        Response.json({
          output: [
            { type: 'web_search_call', status: 'completed' },
            {
              type: 'message',
              content: [
                {
                  type: 'output_text',
                  annotations: [
                    { type: 'url_citation', title: 'unsafe', url: 'javascript:alert(1)' },
                    { type: 'url_citation', title: 'safe', url: 'https://example.com' },
                  ],
                },
              ],
            },
          ],
        }),
      ),
    );
    try {
      const results = await search({ apiKey: 'test' }, { query: 'test' });
      expect(results).toEqual([{ title: 'safe', link: 'https://example.com/', snippet: '' }]);
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it('does not crash on invalid numeric HTML entities', () => {
    expect(decodeEntities('&#99999999; &#x110000; &amp;')).toBe('&#99999999; &#x110000; &');
  });
});
