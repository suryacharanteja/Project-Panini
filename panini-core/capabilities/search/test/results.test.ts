import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { Results } from '@/components/results';
import type { RankedItem } from '@/lib/rank';

function renderResult(publication: Pick<RankedItem, 'publishedDate' | 'ageHours'>): string {
  const lead: RankedItem = {
    id: 'test',
    source: 'web',
    title: 'A result',
    url: 'https://example.com',
    snippet: 'Some content',
    relevance: 0.9,
    ranked: true,
    freshness: 0.5,
    position: 1,
    engines: ['web'],
    ...publication,
  };
  return renderToStaticMarkup(
    createElement(Results, {
      clusters: [{ lead, others: [] }],
      streaming: false,
    }),
  );
}

describe('result publication labels', () => {
  it('renders a day-only date as a calendar date with matching time semantics', () => {
    const html = renderResult({ publishedDate: '2026-09-18', ageHours: 12 });
    expect(html).toContain('<time dateTime="2026-09-18">2026-09-18</time>');
    expect(html).not.toContain('12h ago');
  });

  it('renders a timestamp as relative age while retaining the absolute time', () => {
    expect(renderResult({ publishedDate: '2026-09-18T10:00:00Z', ageHours: 2 })).toContain(
      '<time dateTime="2026-09-18T10:00:00Z">2h ago</time>',
    );
  });

  it('omits the time label when unknown and supports snippet fallback ages', () => {
    expect(renderResult({ ageHours: null })).not.toContain('<time');
    expect(renderResult({ ageHours: 3 })).toContain('<time>3h ago</time>');
  });
});
