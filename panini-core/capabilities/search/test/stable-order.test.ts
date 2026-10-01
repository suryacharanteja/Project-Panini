import { describe, expect, it } from 'vitest';
import { clusterInOrder, type RankedItem } from '@/lib/rank';
import { EMPTY_PLACEMENT, place } from '@/lib/stable-order';

function item(id: string, relevance: number, ranked = true): RankedItem {
  return {
    id,
    source: 'web',
    title: id,
    url: `https://e.com/${id}`,
    snippet: '',
    ageHours: null,
    relevance,
    ranked,
    freshness: 0.5,
    position: 1,
    engines: ['web'],
  };
}

describe('clusterInOrder', () => {
  it('keeps the given order instead of sorting', () => {
    const clusters = clusterInOrder([item('low', 0.1), item('high', 0.9)]);
    expect(clusters.map((c) => c.lead.id)).toEqual(['low', 'high']);
  });
});

describe('place', () => {
  it('shows unranked rows at the bottom, then places them by score once ranked', () => {
    // Engine answered: three rows, unscored, arrival order.
    const found = [item('a', 0, false), item('b', 0, false), item('c', 0, false)];
    let p = place(EMPTY_PLACEMENT, found, 'best', false);
    expect(p.order).toEqual(['a', 'b', 'c']);
    expect(p.placed.size).toBe(0);

    // Judge scored them: b is best, then c, then a.
    const scored = [item('a', 0.4), item('b', 0.9), item('c', 0.7)];
    p = place(p, scored, 'best', false);
    expect(p.order).toEqual(['b', 'c', 'a']);
  });

  it('inserts a later lane by score without moving rows already placed', () => {
    let p = place(EMPTY_PLACEMENT, [item('a', 0.8), item('b', 0.6)], 'best', false);
    // a's score changed by a merge; a and b keep their relative order, c and d
    // are inserted against the scores as they are now.
    p = place(p, [item('a', 0.5), item('b', 0.6), item('c', 0.7), item('d', 0.1)], 'best', false);
    expect(p.order).toEqual(['c', 'a', 'b', 'd']);
  });

  it('re-sorts everything when the mode changes', () => {
    let p = place(EMPTY_PLACEMENT, [item('a', 0.8), item('b', 0.6)], 'best', false);
    p = place(p, [item('a', 0.5), item('b', 0.6)], 'best', true);
    expect(p.order).toEqual(['b', 'a']);
  });

  it('switches between relevance and publication order without changing the results', () => {
    const items = [
      { ...item('relevant', 0.95), ageHours: 72 },
      { ...item('recent', 0.7), ageHours: 1 },
      item('unknown', 0.9),
    ];
    let p = place(EMPTY_PLACEMENT, items, 'best', false);
    expect(p.order).toEqual(['relevant', 'unknown', 'recent']);
    p = place(p, items, 'newest', true);
    expect(p.order).toEqual(['recent', 'relevant', 'unknown']);
    p = place(p, items, 'best', true);
    expect(p.order).toEqual(['relevant', 'unknown', 'recent']);
  });
});
