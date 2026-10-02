/**
 * A small AG-UI client for the console (V2-07): POST a RunAgentInput, read the server-sent events and hand each
 * parsed event to the caller. The console session cookie authenticates through the gateway.
 */

export const AGENT = '/api/agui/agents/panini';

export type ResumeEntry = {
  interruptId: string;
  status: 'resolved' | 'cancelled';
  payload?: unknown;
  metadata?: Record<string, unknown>;
};

export function newId() {
  return typeof crypto !== 'undefined' && 'randomUUID' in crypto
    ? crypto.randomUUID()
    : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}

/** Split an SSE byte stream into `data:` payloads; tolerant of CRLF and partial chunks. */
export function sseEvents(buffer: string): { events: unknown[]; rest: string } {
  const blocks = buffer.replace(/\r\n/g, '\n').split('\n\n');
  const rest = blocks.pop() ?? '';
  const events: unknown[] = [];
  for (const block of blocks) {
    const data = block
      .split('\n')
      .filter((line) => line.startsWith('data:'))
      .map((line) => line.slice(5).trimStart())
      .join('\n');
    if (!data) continue;
    try {
      events.push(JSON.parse(data));
    } catch {
      // A malformed event is skipped; the stream continues.
    }
  }
  return { events, rest };
}

async function stream(
  path: string,
  body: unknown,
  onEvent: (event: any) => void,
  signal?: AbortSignal,
) {
  const response = await fetch(path, {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
    body: JSON.stringify(body),
    signal,
  });
  if (!response.ok || !response.body) {
    const data = await response.json().catch(() => ({}));
    const message =
      response.status === 401
        ? 'Your console session ended. Sign in again from PANINI.'
        : response.status === 404
          ? 'The AG-UI endpoint is off. Turn on "V2-07: agui" in Settings → System → PANINI v2.'
          : (data?.error?.message ?? data?.detail ?? `Request failed (${response.status}).`);
    onEvent({ type: 'RUN_ERROR', message: String(message) });
    return;
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parsed = sseEvents(buffer);
    buffer = parsed.rest;
    parsed.events.forEach(onEvent);
  }
  sseEvents(buffer + '\n\n').events.forEach(onEvent);
}

export function runAgent(
  input: { threadId: string; text?: string; resume?: ResumeEntry[] },
  onEvent: (event: any) => void,
  signal?: AbortSignal,
) {
  return stream(
    `${AGENT}/run`,
    {
      threadId: input.threadId,
      runId: newId(),
      messages: input.text ? [{ id: newId(), role: 'user', content: input.text }] : [],
      tools: [],
      context: [],
      state: {},
      forwardedProps: { panini: { surface: 'console' } },
      ...(input.resume ? { resume: input.resume } : {}),
    },
    onEvent,
    signal,
  );
}

export function connectThread(
  threadId: string,
  onEvent: (event: any) => void,
  signal?: AbortSignal,
) {
  return stream(`${AGENT}/connect`, { threadId }, onEvent, signal);
}

export async function stopThread(threadId: string) {
  await fetch(`${AGENT}/stop`, {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ threadId }),
  });
}
