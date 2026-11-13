import { MessagesSquare } from 'lucide-react';
import { useEffect, useReducer, useRef, useState } from 'react';
import {
  applyEvent,
  CardBoundary,
  emptyConversation,
  InterruptCard,
  ToolCard,
  type Conversation,
} from '../../../shared-ui';
import '../../../shared-ui/tokens.css';
import { connectThread, newId, runAgent, stopThread } from '../agui';
import { Button, Card, Empty, inputClass, Page } from '../components/ui';
import { SaveToSpace } from './spaces/SaveToSpace';
import { api, harness } from '../api';

const THREAD_KEY = 'panini.conversation.thread';

function savedThread(): string {
  try {
    return localStorage.getItem(THREAD_KEY) || '';
  } catch {
    return '';
  }
}

function saveThread(id: string) {
  try {
    localStorage.setItem(THREAD_KEY, id);
  } catch {
    // Storage may be unavailable; the conversation still works for this visit.
  }
}

type Action = { type: 'event'; event: any } | { type: 'user'; text: string } | { type: 'reset' };

function reducer(state: Conversation, action: Action): Conversation {
  if (action.type === 'reset') return emptyConversation();
  if (action.type === 'user')
    return {
      ...state,
      entries: [
        ...state.entries,
        { kind: 'message', id: newId(), role: 'user', text: action.text },
      ],
    };
  return applyEvent(state, action.event);
}

/** Talk to PANINI over AG-UI from the console: tool calls, plan and approvals stream in as cards (V2-07). */
export function ConversationsPage({
  initialThread,
  initialDraft,
}: { initialThread?: string; initialDraft?: string } = {}) {
  const [thread, setThread] = useState(() => initialThread || savedThread() || newId());
  const [state, dispatch] = useReducer(reducer, undefined, emptyConversation);
  const [draft, setDraft] = useState(initialDraft || '');
  const [handoffMessage, setHandoffMessage] = useState('');
  useEffect(() => {
    if (initialDraft) setDraft(initialDraft);
  }, [initialDraft]);
  const abort = useRef<AbortController | null>(null);
  const bottom = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!initialThread) saveThread(thread);
    const controller = new AbortController();
    void connectThread(
      thread,
      (event) => dispatch({ type: 'event', event }),
      controller.signal,
    ).catch(() => {});
    return () => controller.abort();
  }, [thread]);

  useEffect(() => {
    // A block body: newer Chromium returns a Promise from scrollIntoView, and an effect must not return it.
    bottom.current?.scrollIntoView({ block: 'end' });
  }, [state.entries.length]);

  const go = async (input: Parameters<typeof runAgent>[0]) => {
    abort.current?.abort();
    const controller = new AbortController();
    abort.current = controller;
    try {
      await runAgent(input, (event) => dispatch({ type: 'event', event }), controller.signal);
    } catch (error) {
      if ((error as Error).name !== 'AbortError')
        dispatch({
          type: 'event',
          event: { type: 'RUN_ERROR', message: (error as Error).message },
        });
    }
  };

  const send = async () => {
    const text = draft.trim();
    if (!text || state.running) return;
    setDraft(''); // The draft is restored if sending fails below.
    dispatch({ type: 'user', text });
    await go({ threadId: thread, text });
  };

  const decide = (approved: boolean) =>
    go({
      threadId: thread,
      resume: state.interrupts.map((i) => ({
        interruptId: i.id,
        status: 'resolved' as const,
        payload: { approved },
        metadata: { nonce: i.nonce, actionDigest: i.actionDigest },
      })),
    });

  return (
    <Page
      title={initialThread ? 'Page conversation' : 'Conversations'}
      deva="संवाद"
      eyebrow="AG-UI"
      actions={
        !initialThread && (
          <Button
            onClick={() => {
              abort.current?.abort();
              dispatch({ type: 'reset' });
              setThread(newId());
            }}
          >
            New conversation
          </Button>
        )
      }
    >
      <Card
        title={
          <span className="flex items-center gap-2">
            <MessagesSquare size={16} />
            {state.running ? 'PANINI is working…' : 'Talk to PANINI'}
          </span>
        }
      >
        {state.plan.length > 0 && (
          <ol className="mb-3 grid gap-1 text-sm" aria-label="Plan">
            {state.plan.map((s, index) => (
              <li key={index} className="text-muted">
                {s.status === 'completed' ? '✓' : '•'} {s.step}
              </li>
            ))}
          </ol>
        )}
        {state.entries.length === 0 && (
          <Empty>Ask anything. Tool calls and approvals appear here as PANINI works.</Empty>
        )}
        <div className="grid gap-2" aria-live="polite">
          {state.entries.map((entry) => (
            <CardBoundary key={`${entry.kind}-${entry.id}`} label="conversation entry">
              {entry.kind === 'tool' ? (
                <ToolCard call={entry.call} />
              ) : entry.kind === 'activity' ? (
                <p className="text-xs text-faint">{entry.text}</p>
              ) : (
                <p
                  className={
                    entry.role === 'user'
                      ? 'ml-auto max-w-[80%] rounded-lg bg-zinc-3 px-3 py-2 text-sm'
                      : 'max-w-[90%] whitespace-pre-wrap text-sm'
                  }
                >
                  {entry.personaId && (
                    <span className="mb-1 block text-xs text-copper-light">{entry.personaId}</span>
                  )}
                  {entry.text}
                </p>
              )}
            </CardBoundary>
          ))}
          {state.interrupts.map((interrupt) => (
            <InterruptCard
              key={interrupt.id}
              interrupt={interrupt}
              onDecide={decide}
              onReview={(approved, content) =>
                go({
                  threadId: thread,
                  resume: state.interrupts.map((i) => ({
                    interruptId: i.id,
                    status: 'resolved' as const,
                    payload: { approved, ...(i.id === interrupt.id ? { content } : {}) },
                    metadata: { nonce: i.nonce, actionDigest: i.actionDigest },
                  })),
                })
              }
            />
          ))}
          {state.error && (
            <p role="alert" className="text-sm text-danger">
              {state.error}
            </p>
          )}
          <div ref={bottom} />
        </div>
        <form
          className="mt-4 flex gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            void send();
          }}
        >
          <input
            className={inputClass}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="Message PANINI…"
            aria-label="Message PANINI"
          />
          {state.running ? (
            <Button tone="danger" onClick={() => void stopThread(thread)}>
              Stop
            </Button>
          ) : (
            <Button tone="primary" type="submit" disabled={!draft.trim()}>
              Send
            </Button>
          )}
        </form>
        {!state.running && state.entries.length > 0 && (
          <div className="mt-4">
            <Button
              onClick={async () => {
                try {
                  await api(harness('/presence/handoff'), {
                    body: { thread_id: thread, destination: 'phone' },
                  });
                  setHandoffMessage('Queued for your phone.');
                } catch (error) {
                  setHandoffMessage((error as Error).message);
                }
              }}
            >
              Send to my phone
            </Button>
            {handoffMessage && (
              <p role="status" className="text-sm">
                {handoffMessage}
              </p>
            )}
            <SaveToSpace
              key={`${thread}:${state.entries.length}`}
              kind="conversation"
              sourceId={thread}
            />
          </div>
        )}
      </Card>
    </Page>
  );
}
