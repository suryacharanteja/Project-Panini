import { describe, expect, it } from 'vitest';
import {
  formatPublicationAge,
  freshnessScore,
  isPublicationStale,
  parseAgeHours,
  resolvePublication,
  stripAgePrefix,
} from '@/lib/freshness';

describe('structured publication dates', () => {
  const now = Date.parse('2026-09-18T18:30:00Z');

  it('prefers either API shape over a conflicting snippet', () => {
    expect(resolvePublication('2026-09-18T17:30:00Z', '3 days ago ... text', now)).toEqual({
      publishedDate: '2026-09-18T17:30:00Z',
      ageHours: 1,
    });
    expect(resolvePublication('2026-09-17', '1 hour ago ... text', now)).toEqual({
      publishedDate: '2026-09-17',
      ageHours: 42.5,
    });
  });

  it.each([
    undefined,
    null,
    '',
    '1h',
    '6days ago',
    123,
    '2026-02-30',
    '2026-09-18T24:00:00Z',
    '2026-09-18T12:00:00+08:00',
  ])('falls back for missing or invalid API values: %s', (value) => {
    expect(resolvePublication(value, '2 hours ago ... text', now)).toEqual({ ageHours: 2 });
    expect(resolvePublication(value, 'No date', now)).toEqual({ ageHours: null });
  });

  it('clamps future dates without changing the original date', () => {
    expect(resolvePublication('2026-09-19', '', now)).toEqual({
      publishedDate: '2026-09-19',
      ageHours: 0,
    });
  });

  it('only discards a day-only result when its whole day is stale', () => {
    expect(isPublicationStale(resolvePublication('2026-09-17', '', now), 36)).toBe(false);
    expect(isPublicationStale(resolvePublication('2026-09-16', '', now), 36)).toBe(true);
    expect(isPublicationStale(resolvePublication('2026-09-17T00:00:00Z', '', now), 36)).toBe(true);
    expect(isPublicationStale({ ageHours: null }, 36)).toBe(false);
    expect(isPublicationStale(resolvePublication('2025-01-01', '', now), Infinity)).toBe(false);
  });

  it('displays calendar dates without implying hour precision', () => {
    expect(formatPublicationAge(resolvePublication('2026-09-18', '', now))).toBe('2026-09-18');
    expect(formatPublicationAge(resolvePublication('2026-09-17', '', now))).toBe('2026-09-17');
    expect(formatPublicationAge(resolvePublication('2026-09-18T17:30:00Z', '', now))).toBe(
      '1h ago',
    );
    expect(formatPublicationAge({ ageHours: null })).toBeNull();
    expect(formatPublicationAge({ ageHours: 72 })).toBe('3d ago');
  });
});

describe('parseAgeHours', () => {
  it('reads relative prefixes from provider snippets', () => {
    expect(parseAgeHours('19 hours ago ... r/sdforall')).toBe(19);
    expect(parseAgeHours('3 days ago ... I believe')).toBe(72);
    expect(parseAgeHours('1 day ago ... In the next')).toBe(24);
    expect(parseAgeHours('45 minutes ago ... x')).toBeCloseTo(0.75);
  });
  it('reads absolute dates', () => {
    const now = Date.parse('2026-09-17T00:00:00Z');
    expect(parseAgeHours('Sep 12, 2026 ... release', now)).toBeCloseTo(120, 0);
  });
  it('reads ISO dates inside vertical-engine snippets', () => {
    const now = Date.parse('2026-09-17T00:00:00Z');
    expect(parseAgeHours('Gregory Matsnev | 2026-09-15 | Recent position papers', now)).toBe(48);
  });
  it('returns null when there is no date', () => {
    expect(parseAgeHours('Develop, test, run, and bundle')).toBeNull();
  });
});

describe('stripAgePrefix', () => {
  it('drops the prefix and ellipsis', () => {
    expect(stripAgePrefix('19 hours ago ... r/sdforall - Resolume')).toBe('r/sdforall - Resolume');
    expect(stripAgePrefix('Plain snippet')).toBe('Plain snippet');
  });
});

describe('freshnessScore', () => {
  it('decays across the window', () => {
    expect(freshnessScore(0, 24)).toBe(1);
    expect(freshnessScore(12, 24)).toBe(0.5);
    expect(freshnessScore(48, 24)).toBe(0);
    expect(freshnessScore(null, 24)).toBe(0.35);
    expect(freshnessScore(2000, Number.POSITIVE_INFINITY)).toBe(0.5);
  });
});

import { decodeEntities } from '@/lib/web-search';

describe('decodeEntities', () => {
  it('decodes entities in source titles and snippets', () => {
    expect(
      decodeEntities('Execute the&nbsp;... 3 &middot; 6 &middot; 1.3K &amp; &#39;x&#39; &#x2019;'),
    ).toBe("Execute the ... 3 · 6 · 1.3K & 'x' ’");
  });
});
