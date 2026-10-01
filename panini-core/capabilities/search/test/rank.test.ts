import { describe, expect, it } from 'vitest';
import { canonicalUrl, clusterItems, compareItems, titleKey, type RankedItem } from '@/lib/rank';

function item(partial: Partial<RankedItem> & { id: string }): RankedItem {
  return {
    source: 'web',
    title: 'Title',
    url: `https://example.com/${partial.id}`,
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

describe('titleKey', () => {
  it('drops site suffixes', () => {
    expect(titleKey('Should I move away from Bun? - Reddit')).toBe(
      titleKey('Should I move away from Bun?'),
    );
    expect(titleKey('Bear 2.10: Hello SiriAI : r/bearapp - Reddit')).toBe('bear 2 10 hello siriai');
  });
});

describe('canonicalUrl', () => {
  it('normalizes host, trailing slash and query', () => {
    expect(canonicalUrl('https://www.reddit.com/r/bun/comments/1/x/?utm_source=1#top')).toBe(
      'reddit.com/r/bun/comments/1/x',
    );
    expect(canonicalUrl('https://twitter.com/a/status/1')).toBe('x.com/a/status/1');
    expect(canonicalUrl('https://www.youtube.com/watch?v=abc&feature=share')).toBe(
      'youtube.com/watch?v=abc',
    );
    expect(canonicalUrl('https://www.youtube.com/watch?v=abc')).not.toBe(
      canonicalUrl('https://www.youtube.com/watch?v=xyz'),
    );
  });
});

describe('clusterItems', () => {
  it('orders by composite and groups duplicates', () => {
    const a = item({ id: 'a', relevance: 0.9, title: 'Bun 1.3 released' });
    const b = item({ id: 'b', relevance: 0.2, title: 'Hair bun tutorial' });
    const c = item({
      id: 'c',
      relevance: 0.8,
      title: 'Bun 1.3 released - Reddit',
      url: 'https://reddit.com/x',
    });
    const clusters = clusterItems([b, a, c]);
    expect(clusters.map((cl) => cl.lead.id)).toEqual(['a', 'b']);
    expect(clusters[0]!.others.map((o) => o.id)).toEqual(['c']);
  });
  it('orders by the shown percentage, then engine agreement, then engine rank', () => {
    const a = item({ id: 'a', relevance: 0.84, freshness: 0.1, position: 5 });
    const b = item({
      id: 'b',
      relevance: 0.78,
      freshness: 1,
      position: 1,
      engines: ['web', 'duckduckgo'],
    });
    const c = item({ id: 'c', relevance: 0.841, position: 1 });
    expect([b, a, c].sort((x, y) => compareItems(x, y, 'best')).map((i) => i.id)).toEqual([
      'c',
      'a',
      'b',
    ]);
    const old = item({ id: 'old', relevance: 0.9, ageHours: 100 });
    const fresh = item({ id: 'fresh', relevance: 0.6, ageHours: 2 });
    const unknown = item({ id: 'unknown', relevance: 0.95, ageHours: null });
    expect(
      [old, unknown, fresh].sort((x, y) => compareItems(x, y, 'newest')).map((i) => i.id),
    ).toEqual(['fresh', 'old', 'unknown']);
  });
});
