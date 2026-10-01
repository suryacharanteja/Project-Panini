import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { Filters } from '@/components/filters';
import { SOURCE_IDS, type SourceId } from '@/lib/sources';
import type { AskState } from '@/lib/use-ask';

function renderGoogle(error?: string) {
  const state: AskState = {
    phase: 'done',
    items: [],
    totalMs: 15_000,
    message: null,
    found: { 'web/openai': 0 },
    lanes: {
      'web/openai': {
        type: 'lane',
        source: 'web',
        engine: 'openai',
        items: [],
        stale: 0,
        searchMs: 15_000,
        scoreMs: 0,
        ...(error ? { error } : {}),
      },
    },
    intent: {
      type: 'intent',
      request: 'TypeSafe Jev news in the past 24 hours',
      query: 'TypeSafe Jev',
      entityQuery: 'TypeSafe Jev',
      candidates: ['TypeSafe Jev'],
      window: '24h',
      sources: ['web'],
      intentMs: 100,
      inferred: {
        window: { choice: '24h', confidence: 1 },
        sources: Object.fromEntries(SOURCE_IDS.map((id) => [id, id === 'web' ? 1 : 0])) as Record<
          SourceId,
          number
        >,
        query: { index: 0, confidence: 1 },
        entity: { index: 0, confidence: 1 },
      },
    },
  };
  return renderToStaticMarkup(
    createElement(Filters, {
      state,
      explicitWindow: undefined,
      explicitSources: undefined,
      onWindow: () => undefined,
      onSources: () => undefined,
      onReset: () => undefined,
    }),
  );
}

describe('source result counts', () => {
  it('does not present a timed-out source as zero results', () => {
    const html = renderGoogle('The operation was aborted due to timeout');
    expect(html).toContain('Web: search failed');
    expect(html).not.toContain('>0</span>');
  });

  it('still displays zero for a successful search with no results', () => {
    const html = renderGoogle();
    expect(html).not.toContain('Web: search failed');
    expect(html).toContain('>0</span>');
  });
});
