import { afterEach, describe, expect, it, vi } from 'vitest';
import { runSearch } from '@/lib/pipeline';
import { memoryCache } from '@/lib/cache';
import { compareItems } from '@/lib/rank';

const searchMock = vi.hoisted(() => vi.fn());
const intentMock = vi.hoisted(() => vi.fn());
const rankMock = vi.hoisted(() => vi.fn());
vi.mock('@/lib/web-search', () => ({ search: searchMock }));
vi.mock('@/lib/jev', () => ({ inferIntent: intentMock, rerank: rankMock }));
const deps = { retrieval: { apiKey: 'openai' }, judge: { apiKey: 'gateway' } };
function stubFetch(results?: Record<string, unknown>[]) {
  intentMock.mockResolvedValue({
    window: { choice: '7d', confidence: 0.9 },
    sources: { reddit: 0.9 },
    query: { index: 1, confidence: 0.9 },
    entity: { index: 0, confidence: 0.9 },
    usage: { input_tokens: 100, output_tokens: 0 },
  });
  searchMock.mockResolvedValue(
    results ?? [
      {
        title: 'Bun runtime',
        link: 'https://reddit.com/r/bun/1',
        snippet: '3 days ago ... runtime',
      },
      { title: 'Hair bun', link: 'https://reddit.com/r/hair/2', snippet: '1 day ago ... hair' },
    ],
  );
  rankMock.mockImplementation(async (_config, _request, items) => ({
    relevance: Object.fromEntries(
      items.map((item: { id: string }, i: number) => [item.id, i ? 0.05 : 0.95]),
    ),
    usage: { input_tokens: 100, output_tokens: 0 },
  }));
}
afterEach(() => vi.resetAllMocks());

describe('runSearch', () => {
  it('carries API dates through filtering, scoring and Newest sorting', async () => {
    stubFetch([
      {
        title: 'Old',
        link: 'https://a.com/old',
        snippet: '1 hour ago ... misleading',
        published_date: '2025-01-01',
      },
      {
        title: 'Day only',
        link: 'https://a.com/day',
        snippet: 'plain',
        published_date: '2026-09-17',
      },
      {
        title: 'Recent',
        link: 'https://a.com/recent',
        snippet: '3 days ago ... misleading',
        published_date: '2026-09-18T17:30:00Z',
      },
      { title: 'Unknown', link: 'https://a.com/unknown', snippet: 'plain', published_date: null },
    ]);
    const out = await runSearch(
      {
        retrieval: { apiKey: 's1' },
        judge: { apiKey: 'ts' },
        now: () => new Date('2026-09-18T18:30:00Z'),
      },
      { request: 'Bun', sources: ['web'], window: '24h' },
    );
    expect(out.lanes[0]!.stale).toBe(1);
    expect(out.items).toHaveLength(3);
    expect(out.items.find((i) => i.title === 'Day only')).toMatchObject({
      publishedDate: '2026-09-17',
      ageHours: 42.5,
    });
    const recent = out.items.find((i) => i.title === 'Recent')!;
    expect(recent).toMatchObject({ publishedDate: '2026-09-18T17:30:00Z', ageHours: 1 });
    expect(recent.freshness).toBeCloseTo(23 / 24);
    expect([...out.items].sort((a, b) => compareItems(a, b, 'newest')).map((i) => i.title)).toEqual(
      ['Recent', 'Day only', 'Unknown'],
    );
  });

  it('recomputes age from the cached absolute publication time', async () => {
    stubFetch([
      {
        title: 'Recent',
        link: 'https://a.com',
        snippet: '1 hour ago ... text',
        published_date: '2026-09-18T17:00:00Z',
      },
    ]);
    let now = new Date('2026-09-18T18:00:00Z');
    const deps = {
      retrieval: { apiKey: 's1' },
      judge: { apiKey: 'ts' },
      cache: memoryCache(),
      now: () => now,
    };
    const input = { request: 'Bun', sources: ['web' as const], window: 'any' as const };
    expect((await runSearch(deps, input)).items[0]!.ageHours).toBe(1);
    now = new Date('2026-09-18T18:30:00Z');
    expect((await runSearch(deps, input)).items[0]!.ageHours).toBe(1.5);
    expect(searchMock).toHaveBeenCalledTimes(1);
  });

  it('infers intent and streams one real provider lane per source', async () => {
    stubFetch();
    const out = await runSearch(
      { ...deps, now: () => new Date('2026-09-17') },
      { request: 'what are people saying about Bun 1.3 this week' },
    );
    expect(out.sources).toEqual(['reddit']);
    expect(out.query).toBe('Bun 1.3');
    expect(searchMock).toHaveBeenCalledTimes(1);
    expect(searchMock.mock.calls[0]![1]).toMatchObject({
      service: 'openai',
      includeSites: ['reddit.com'],
      timeRange: 'week',
    });
    expect(out.items[0]).toMatchObject({ relevance: 0.95, ranked: true, ageHours: 72 });
    expect(out.tokens).toBe(200);
  });
  it('honors explicit source and time overrides without speculative paid requests', async () => {
    stubFetch();
    const out = await runSearch(deps, {
      request: 'Bun',
      sources: ['web', 'github'],
      window: '24h',
    });
    expect(out.sources).toEqual(['web', 'github']);
    expect(searchMock).toHaveBeenCalledTimes(2);
    expect(searchMock.mock.calls.map((call) => call[1])).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ includeSites: [], timeRange: 'day' }),
        expect.objectContaining({ includeSites: ['github.com'], timeRange: 'day' }),
      ]),
    );
  });
  it.each([
    ['hackernews', 'news.ycombinator.com'],
    ['arxiv', 'arxiv.org'],
    ['youtube', 'youtube.com'],
  ])('restricts %s to its own domain', async (source, domain) => {
    stubFetch();
    await runSearch(deps, { request: 'test', sources: [source as 'hackernews'], window: 'any' });
    expect(searchMock.mock.calls[0]![1]).toMatchObject({
      includeSites: [domain],
      timeRange: undefined,
    });
  });
  it('keeps useful results when another source fails', async () => {
    stubFetch();
    searchMock.mockRejectedValueOnce(new Error('upstream unavailable'));
    const out = await runSearch(deps, { request: 'test', sources: ['github', 'reddit'] });
    expect(out.errors).toHaveLength(1);
    expect(out.errors[0]!.source).toBe('github');
    expect(out.items).toHaveLength(2);
  });
  it('preserves unranked results when Jev fails after retrieval', async () => {
    stubFetch();
    rankMock.mockRejectedValue(new Error('judge unavailable'));
    const out = await runSearch(deps, { request: 'test', sources: ['reddit'] });
    expect(out.items).toHaveLength(2);
    expect(out.items.every((item) => !item.ranked)).toBe(true);
    expect(out.errors[0]!.message).toContain('judge unavailable');
  });
  it('limits inferred sources to the three strongest signals', async () => {
    stubFetch();
    intentMock.mockResolvedValueOnce({
      window: { choice: 'any', confidence: 0.9 },
      sources: { web: 0.7, reddit: 0.8, github: 0.9, arxiv: 0.95 },
      query: { index: 0 },
      entity: { index: 0 },
      usage: { input_tokens: 1 },
    });
    const out = await runSearch(deps, { request: 'test' });
    expect(out.sources).toEqual(['arxiv', 'github', 'reddit']);
    expect(searchMock).toHaveBeenCalledTimes(3);
  });
});
