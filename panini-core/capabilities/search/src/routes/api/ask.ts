import { createFileRoute } from '@tanstack/react-router';
import { askStream, type AskEvent } from '@/lib/pipeline';
import { validateAskRequest } from '@/lib/validate';
import { getEnv } from '@/server/env.server';

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' },
  });
}

function sameOrigin(request: Request): boolean {
  const origin = request.headers.get('origin');
  if (!origin) return request.headers.get('sec-fetch-site') === 'same-origin';
  return origin === new URL(request.url).origin;
}

/**
 * POST /api/ask → newline-delimited JSON, one AskEvent per line, in the order
 * they happen: intent, then each source as it finishes, then done.
 */
export const Route = createFileRoute('/api/ask')({
  server: {
    handlers: {
      POST: async ({ request }) => {
        if (!sameOrigin(request)) return json(403, { error: 'forbidden' });

        let env: ReturnType<typeof getEnv>;
        try {
          env = getEnv();
        } catch (error) {
          return json(503, {
            error: error instanceof Error ? error.message : 'Missing configuration',
          });
        }

        let data: ReturnType<typeof validateAskRequest>;
        try {
          data = validateAskRequest(await request.json());
        } catch (error) {
          return json(400, { error: error instanceof Error ? error.message : 'Bad request' });
        }

        const deps = {
          retrieval: { apiKey: env.OPENAI_API_KEY },
          judge: { apiKey: env.AI_GATEWAY_API_KEY },
          cache: env.CACHE,
        };
        const cancelled = new AbortController();
        const signal = AbortSignal.any([
          request.signal,
          cancelled.signal,
          AbortSignal.timeout(90_000),
        ]);
        const encoder = new TextEncoder();
        let closed = false;
        const stream = new ReadableStream<Uint8Array>({
          cancel() {
            closed = true;
            cancelled.abort();
          },
          async start(controller) {
            const send = (event: AskEvent | { type: 'error'; message: string }) => {
              if (!closed) controller.enqueue(encoder.encode(`${JSON.stringify(event)}\n`));
            };
            try {
              for await (const event of askStream(
                deps,
                { request: data.q, window: data.w, sources: data.s },
                signal,
              )) {
                send(event);
              }
            } catch (error) {
              const message = error instanceof Error ? error.message : 'Search failed';
              console.error('[ask] failed', message);
              send({ type: 'error', message });
            } finally {
              if (!closed) {
                closed = true;
                controller.close();
              }
            }
          },
        });
        return new Response(stream, {
          headers: {
            'Content-Type': 'application/x-ndjson; charset=utf-8',
            'Cache-Control': 'no-store',
            'X-Accel-Buffering': 'no',
          },
        });
      },
    },
  },
});
