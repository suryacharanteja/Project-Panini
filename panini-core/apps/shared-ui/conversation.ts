import type { InterruptView, ToolCall } from './cards';
import { toText } from './text';

/**
 * Folds AG-UI events into what a conversation view shows: messages, tool calls, the plan and open interrupts.
 * Unknown or malformed events are ignored, so one bad event never breaks the view.
 */

export type Entry =
  | { kind: 'message'; id: string; role: 'user' | 'assistant'; text: string; personaId?: string }
  | { kind: 'tool'; id: string; call: ToolCall }
  | { kind: 'activity'; id: string; text: string };

export type Conversation = {
  entries: Entry[];
  plan: { step: string; status: string }[];
  interrupts: (InterruptView & { nonce?: string; actionDigest?: string; toolCallId?: string })[];
  running: boolean;
  error: string;
  result: { outcome: string; message: string } | null;
};

export const emptyConversation = (): Conversation => ({
  entries: [],
  plan: [],
  interrupts: [],
  running: false,
  error: '',
  result: null,
});

type AnyEvent = Record<string, any>;

export function applyEvent(state: Conversation, event: AnyEvent): Conversation {
  if (!event || typeof event !== 'object' || typeof event.type !== 'string') return state;
  const entries = state.entries;
  switch (event.type) {
    case 'RUN_STARTED':
      return { ...state, running: true, error: '', result: null, interrupts: [] };
    case 'TEXT_MESSAGE_START':
      return {
        ...state,
        entries: [
          ...entries,
          { kind: 'message', id: toText(event.messageId), role: 'assistant', text: '' },
        ],
      };
    case 'TEXT_MESSAGE_CONTENT':
      return {
        ...state,
        entries: entries.map((e) =>
          e.kind === 'message' && e.id === toText(event.messageId)
            ? { ...e, text: e.text + toText(event.delta) }
            : e,
        ),
      };
    case 'TOOL_CALL_START': {
      const id = toText(event.toolCallId);
      const call = { id, name: toText(event.toolCallName) };
      // A resumed run starts its pending call again under the same id: update that card, never add a twin.
      if (entries.some((e) => e.kind === 'tool' && e.id === id))
        return {
          ...state,
          entries: entries.map((e) => (e.kind === 'tool' && e.id === id ? { ...e, call } : e)),
        };
      return { ...state, entries: [...entries, { kind: 'tool', id, call }] };
    }
    case 'TOOL_CALL_ARGS':
      return {
        ...state,
        entries: entries.map((e) =>
          e.kind === 'tool' && e.id === toText(event.toolCallId)
            ? { ...e, call: { ...e.call, args: parse(event.delta) } }
            : e,
        ),
      };
    case 'TOOL_CALL_RESULT':
      return {
        ...state,
        entries: entries.map((e) =>
          e.kind === 'tool' && e.id === toText(event.toolCallId)
            ? { ...e, call: { ...e.call, output: parse(event.content) } }
            : e,
        ),
      };
    case 'STATE_SNAPSHOT':
      return {
        ...state,
        plan: Array.isArray(event.snapshot?.plan) ? event.snapshot.plan.map(step) : state.plan,
      };
    case 'STATE_DELTA': {
      const patch = Array.isArray(event.delta)
        ? event.delta.find((op: AnyEvent) => op?.path === '/plan')
        : null;
      return patch && Array.isArray(patch.value)
        ? { ...state, plan: patch.value.map(step) }
        : state;
    }
    case 'CUSTOM':
      if (event.name === 'panini.persona' && event.value?.messageId && event.value?.personaId)
        return {
          ...state,
          entries: entries.map((e) =>
            e.kind === 'message' && e.id === event.value.messageId
              ? { ...e, personaId: toText(event.value.personaId, 41) }
              : e,
          ),
        };
      if (event.name === 'panini.activity' && event.value?.message)
        return {
          ...state,
          entries: [
            ...entries,
            { kind: 'activity', id: `a${entries.length}`, text: toText(event.value.message, 300) },
          ],
        };
      return state;
    case 'RUN_FINISHED': {
      const interrupts =
        event.outcome?.type === 'interrupt' && Array.isArray(event.outcome.interrupts)
          ? event.outcome.interrupts.map((i: AnyEvent) => ({
              id: toText(i.id),
              reason: toText(i.reason),
              message: toText(i.message),
              toolCallId: toText(i.toolCallId),
              kind: toText(i.metadata?.kind),
              title: toText(i.metadata?.title),
              preview: i.metadata?.preview,
              level: toText(i.metadata?.level),
              nonce: toText(i.metadata?.nonce),
              actionDigest: toText(i.metadata?.actionDigest),
              expiresAt: toText(i.expiresAt),
            }))
          : [];
      const tools = interrupts.length
        ? entries
        : entries.map((e) =>
            e.kind === 'tool' && e.call.output === undefined
              ? { ...e, call: { ...e.call, state: 'interrupted' as const } }
              : e,
          );
      return {
        ...state,
        entries: tools,
        running: false,
        interrupts,
        result: event.result
          ? { outcome: toText(event.result.outcome), message: toText(event.result.message) }
          : null,
      };
    }
    case 'RUN_ERROR':
      return { ...state, running: false, error: toText(event.message, 500) || 'The run failed.' };
    case 'MESSAGES_SNAPSHOT':
      return {
        ...state,
        entries: (Array.isArray(event.messages) ? event.messages : [])
          .filter((m: AnyEvent) => m?.role === 'user' || m?.role === 'assistant')
          .map((m: AnyEvent) => ({
            kind: 'message',
            id: toText(m.id),
            role: m.role,
            text: toText(m.content, 8000),
          })),
      };
    default:
      return state;
  }
}

function step(value: AnyEvent) {
  return { step: toText(value?.step, 300), status: toText(value?.status, 40) };
}

function parse(value: unknown): unknown {
  if (typeof value !== 'string') return value;
  try {
    return JSON.parse(value);
  } catch {
    return value;
  }
}
