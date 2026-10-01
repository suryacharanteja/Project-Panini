import { afterEach, expect, it, vi } from 'vitest';
import { readResults, search } from '@/lib/web-search';
afterEach(() => vi.unstubAllGlobals());
it('only renders retrieved URLs and enforces domain boundaries', () => {
  const body = {
    output: [
      {
        type: 'web_search_call',
        status: 'completed',
        action: {
          sources: [{ url: 'https://evilgithub.com/a' }, { url: 'https://github.com/org/repo' }],
        },
      },
      {
        type: 'message',
        content: [
          {
            type: 'output_text',
            text: '[Invented](https://invented.example) summary',
            annotations: [
              {
                type: 'url_citation',
                url: 'https://github.com/org/repo?utm_source=openai',
                title: 'Repo',
              },
            ],
          },
        ],
      },
    ],
  };
  expect(readResults(body, { query: 'test', includeSites: ['github.com'] })).toEqual([
    { title: 'Repo', link: 'https://github.com/org/repo', snippet: '' },
  ]);
});
it('requires a completed web tool call', () => {
  expect(() => readResults({ output: [] }, { query: 'test' })).toThrow('no completed retrieval');
});
it('uses the existing OpenAI key and web_search with actual sources', async () => {
  const fetcher = vi.fn(async () =>
    Response.json({ output: [{ type: 'web_search_call', status: 'completed' }] }),
  );
  vi.stubGlobal('fetch', fetcher);
  await search({ apiKey: 'existing-key' }, { query: 'repos', includeSites: ['github.com'] });
  const [url, init] = fetcher.mock.calls[0] as unknown as [string, RequestInit];
  expect(url).toBe('https://api.openai.com/v1/responses');
  expect(init.headers).toMatchObject({ Authorization: 'Bearer existing-key' });
  expect(JSON.parse(String(init.body))).toMatchObject({
    store: false,
    tool_choice: 'required',
    include: ['web_search_call.action.sources'],
    tools: [{ type: 'web_search' }],
  });
  expect(JSON.parse(JSON.parse(String(init.body)).input).query).toBe('(site:github.com) repos');
});
it('redacts upstream failures', async () => {
  vi.stubGlobal('fetch', async () => new Response('secret provider request', { status: 503 }));
  await expect(search({ apiKey: 'key' }, { query: 'test' })).rejects.toThrow(
    'temporarily unavailable',
  );
});
