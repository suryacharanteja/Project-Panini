import { describe, expect, it } from 'vitest';
import { mergeItems } from '@/lib/merge';
import type { RankedItem } from '@/lib/rank';

function item(partial: Partial<RankedItem> & { id: string; url: string }): RankedItem {
  return {
    source: 'web',
    title: 't',
    snippet: '',
    ageHours: null,
    relevance: 0.5,
    ranked: true,
    freshness: 0.5,
    position: 1,
    engines: ['web'],
    ...partial,
  };
}

describe('mergeItems', () => {
  it('prefers structured dates in either arrival order and keeps freshness consistent', () => {
    const estimated = item({ id: 'a', url: 'https://a.com/p', ageHours: 1, freshness: 0.95 });
    const dated = item({
      id: 'b',
      url: 'https://a.com/p',
      publishedDate: '2026-09-17',
      ageHours: 42,
      freshness: 0,
    });
    for (const [first, second] of [
      [estimated, dated],
      [dated, estimated],
    ]) {
      expect(mergeItems([first!], [second!])[0]).toMatchObject({
        publishedDate: '2026-09-17',
        ageHours: 42,
        freshness: 0,
      });
    }
  });

  it('fills a missing date and retains the first structured date on conflict', () => {
    const unknown = item({ id: 'a', url: 'https://a.com/p' });
    const dated = item({
      id: 'b',
      url: 'https://a.com/p',
      publishedDate: '2026-09-17T10:00:00Z',
      ageHours: 2,
      freshness: 0.9,
    });
    const conflicting = item({
      id: 'c',
      url: 'https://a.com/p',
      publishedDate: '2026-09-18',
      ageHours: 1,
      freshness: 1,
    });
    expect(mergeItems([unknown], [dated, conflicting])[0]).toMatchObject({
      publishedDate: dated.publishedDate,
      ageHours: 2,
      freshness: 0.9,
    });
  });

  it('folds the same URL from a second engine into one row', () => {
    const a = item({
      id: 'a',
      url: 'https://x.com/p/1',
      relevance: 0.6,
      position: 3,
      engines: ['web'],
    });
    const b = item({
      id: 'b',
      url: 'https://www.x.com/p/1/',
      relevance: 0.9,
      position: 1,
      engines: ['duckduckgo'],
      ageHours: 5,
      freshness: 0.9,
    });
    const c = item({ id: 'c', url: 'https://x.com/p/2', engines: ['duckduckgo'] });
    const out = mergeItems([a], [b, c]);
    expect(out).toHaveLength(2);
    expect(out[0]).toMatchObject({
      id: 'a',
      relevance: 0.9,
      position: 1,
      engines: ['web', 'duckduckgo'],
      ageHours: 5,
      freshness: 0.9,
    });
    expect(out[1]!.id).toBe('c');
  });
});
