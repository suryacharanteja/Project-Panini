import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { search } from '@/lib/web-search';

beforeEach(() => {
  vi.useFakeTimers();
  // Node's native AbortSignal timer does not use Vitest's fake clock.
  vi.spyOn(AbortSignal, 'timeout').mockImplementation((ms) => {
    const controller = new AbortController();
    setTimeout(() => controller.abort(new DOMException('Search timed out', 'TimeoutError')), ms);
    return controller.signal;
  });
});

afterEach(() => {
  vi.clearAllTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

function slowSearch(delay: number) {
  vi.stubGlobal(
    'fetch',
    vi.fn(
      (_url: string, init: RequestInit) =>
        new Promise<Response>((resolve, reject) => {
          const signal = init.signal!;
          const timer = setTimeout(() => {
            signal.removeEventListener('abort', abort);
            resolve(
              new Response(
                JSON.stringify({
                  output: [
                    { type: 'web_search_call', status: 'completed' },
                    {
                      type: 'message',
                      content: [
                        {
                          type: 'output_text',
                          text: '1 hour ago · Jev',
                          annotations: [
                            {
                              type: 'url_citation',
                              title: 'TypeSafe Jev',
                              url: 'https://typesafe.ai/',
                              start_index: 16,
                            },
                          ],
                        },
                      ],
                    },
                  ],
                }),
              ),
            );
          }, delay);
          function abort() {
            clearTimeout(timer);
            reject(signal.reason);
          }
          if (signal.aborted) abort();
          else signal.addEventListener('abort', abort, { once: true });
        }),
    ),
  );
}

describe('search deadline', () => {
  it('keeps results from a filtered web request that takes eight seconds', async () => {
    slowSearch(8_000);
    const result = search(
      { apiKey: 'test' },
      {
        query: 'TypeSafe Jev',
        service: 'web',
        timeRange: 'day',
        excludeSites: ['news.ycombinator.com', 'reddit.com', 'github.com'],
      },
    );
    const check = expect(result).resolves.toEqual([
      { title: 'TypeSafe Jev', link: 'https://typesafe.ai/', snippet: '1 hour ago · Jev' },
    ]);
    await vi.advanceTimersByTimeAsync(8_000);
    await check;
  });

  it('still stops a stalled engine after forty-five seconds', async () => {
    slowSearch(50_000);
    const result = search({ apiKey: 'test' }, { query: 'TypeSafe Jev' });
    const check = expect(result).rejects.toThrow('timed out');
    await vi.advanceTimersByTimeAsync(45_000);
    await check;
  });

  it('respects cancellation before the engine deadline', async () => {
    slowSearch(8_000);
    const controller = new AbortController();
    const result = search({ apiKey: 'test' }, { query: 'TypeSafe Jev' }, controller.signal);
    const check = expect(result).rejects.toThrow('cancelled');
    await vi.advanceTimersByTimeAsync(500);
    controller.abort();
    await check;
  });
});
