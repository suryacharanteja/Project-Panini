/** Private stdio adapter. No HTTP listener and no credentials in output. */
import { createInterface } from 'node:readline';
import { askStream } from './lib/pipeline';
import { memoryCache } from './lib/cache';
const cache = memoryCache();
const jobs = new Map<string, AbortController>();
const send = (value: unknown) => process.stdout.write(JSON.stringify(value) + '\n');
const input = createInterface({ input: process.stdin, crlfDelay: Infinity });
input.on('line', (line) => {
  if (line.length > 8192) return;
  let request: { id: string; op: string; request?: string };
  try {
    request = JSON.parse(line);
  } catch {
    return;
  }
  if (typeof request.id !== 'string' || request.id.length > 100) return;
  if (request.op === 'cancel') {
    jobs.get(request.id)?.abort();
    return;
  }
  if (
    request.op !== 'search' ||
    typeof request.request !== 'string' ||
    !request.request.trim() ||
    request.request.length > 300 ||
    jobs.size
  ) {
    send({ id: request.id, type: 'error', code: 'INVALID_REQUEST' });
    return;
  }
  const controller = new AbortController();
  jobs.set(request.id, controller);
  void (async () => {
    try {
      const retrieval = process.env.OPENAI_API_KEY;
      const judge = process.env.AI_GATEWAY_API_KEY;
      if (!retrieval || !judge) throw new Error('configuration');
      for await (const event of askStream(
        { retrieval: { apiKey: retrieval }, judge: { apiKey: judge }, cache },
        { request: request.request! },
        AbortSignal.any([controller.signal, AbortSignal.timeout(90000)]),
      )) {
        send({ id: request.id, ...event });
      }
    } catch {
      send({
        id: request.id,
        type: 'error',
        code: controller.signal.aborted ? 'CANCELLED' : 'SEARCH_UNAVAILABLE',
      });
    } finally {
      jobs.delete(request.id);
      send({ id: request.id, type: 'end' });
    }
  })();
});
input.on('close', () => {
  for (const job of jobs.values()) job.abort();
});
