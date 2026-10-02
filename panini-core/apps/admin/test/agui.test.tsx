import { EventSchemas } from '@ag-ui/core/schemas';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { createElement } from 'react';
import { renderToString } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import {
  applyEvent,
  CardBoundary,
  ChoiceCard,
  emptyConversation,
  InterruptCard,
  QuestionCard,
  ReceiptChip,
  TakeoverCard,
  ToolCard,
  toolDetail,
  toolLabel,
  toText,
} from '../../shared-ui';
import { sseEvents } from '../src/agui';

const fixture = (name: string) =>
  sseEvents(readFileSync(join(__dirname, 'fixtures', `agui-${name}.sse`), 'utf-8') + '\n\n')
    .events as any[];

describe('AG-UI conformance (V2-07)', () => {
  // Recorded from the real endpoint by scripts/agui-fixture.py: a tool call, an approval interrupt and its resume.
  for (const name of ['run-interrupt', 'run-resume', 'connect']) {
    it(`every event PANINI streams in ${name} matches the official @ag-ui/core schema`, () => {
      const events = fixture(name);
      expect(events.length).toBeGreaterThan(1);
      for (const event of events) {
        const parsed = EventSchemas.safeParse(event);
        if (!parsed.success) throw new Error(`${event.type}: ${parsed.error.message}`);
      }
    });
  }

  it('an interrupted run ends with the interrupt and the proof a resume must echo', () => {
    const events = fixture('run-interrupt');
    expect(events[0].type).toBe('RUN_STARTED');
    const last = events.at(-1);
    expect(last.type).toBe('RUN_FINISHED');
    expect(last.outcome.type).toBe('interrupt');
    expect(last.outcome.interrupts[0].metadata).toMatchObject({ kind: 'approval', nonce: 'NONCE' });
    expect(last.outcome.interrupts[0].metadata.actionDigest).toMatch(/^[a-f0-9]{64}$/);
  });

  it('folds a whole run into the conversation view', () => {
    let state = emptyConversation();
    for (const event of fixture('run-interrupt')) state = applyEvent(state, event);
    expect(state.running).toBe(false);
    expect(state.interrupts).toHaveLength(1);
    expect(state.plan[0]?.step).toBe('Write the note');
    const tools = state.entries.filter((e) => e.kind === 'tool');
    expect(tools.map((t) => (t.kind === 'tool' ? t.call.name : ''))).toEqual([
      'update_plan',
      'run_command',
    ]);
    for (const event of fixture('run-resume')) state = applyEvent(state, event);
    expect(state.interrupts).toHaveLength(0);
    expect(state.result).toEqual({ outcome: 'verified_complete', message: 'Saved.' });
    // The resumed run starts the parked call again under the same id: one card, now finished.
    const after = state.entries.filter((e) => e.kind === 'tool');
    expect(after.map((t) => (t.kind === 'tool' ? t.call.name : ''))).toEqual([
      'update_plan',
      'run_command',
      'finish',
    ]);
    const command = after[1];
    expect(command?.kind === 'tool' && command.call.output !== undefined).toBe(true);
  });
});

describe('cards never break the interface', () => {
  const html = (node: any) => renderToString(node);
  const odd = [null, undefined, 42, { nested: { deep: true } }, ['a', { b: 1 }], 'plain'];

  it('renders tool cards for every payload shape', () => {
    for (const value of odd) {
      const out = html(
        createElement(ToolCard, {
          call: { id: 'x', name: value as any, args: value, output: value },
        }),
      );
      expect(out).toContain('pc-tool');
    }
    expect(
      html(
        createElement(ToolCard, {
          call: { id: 'e', name: 'run_command', output: { error: 'denied' } },
        }),
      ),
    ).toContain('Needs attention');
  });

  it('renders interrupt, question, choice, take-over and receipt cards with malformed props', () => {
    for (const value of odd) {
      const interrupt = {
        id: 'i',
        title: value,
        message: value,
        preview: value,
        level: value,
      } as any;
      expect(html(createElement(InterruptCard, { interrupt, onDecide: () => {} }))).toContain(
        'Approve',
      );
      expect(
        html(createElement(QuestionCard, { question: interrupt, onAnswer: () => {} })),
      ).toContain('Answer');
      expect(
        html(
          createElement(ChoiceCard, { title: value, choices: value as any, onChoose: () => {} }),
        ),
      ).toContain('pc-choice');
      expect(html(createElement(TakeoverCard, { message: value, onDone: () => {} }))).toContain(
        'Your turn',
      );
      expect(
        html(createElement(ReceiptChip, { receipt: { id: value, tool: value } as any })),
      ).toContain('pc-receipt');
    }
  });

  it('a boundary hides a card that throws instead of the page', () => {
    const boundary = new CardBoundary({ children: 'x' });
    boundary.state = CardBoundary.getDerivedStateFromError();
    expect(boundary.render()).toBeNull();
  });

  it('ignores events it does not understand', () => {
    const state = emptyConversation();
    for (const event of [
      null,
      7,
      'x',
      { type: 9 },
      { type: 'NOPE' },
      { type: 'TEXT_MESSAGE_CONTENT' },
    ])
      expect(applyEvent(state, event as any)).toEqual(state);
  });

  it('labels tools in plain words from redacted views', () => {
    expect(toolLabel('run_command')).toBe('Running a command');
    expect(toolLabel('client_show_chart')).toBe('In your app: show chart');
    expect(toolDetail({ command: { preview: 'Get-Date', chars: 8 } })).toBe('Get-Date');
    expect(toText({ a: 1 })).toBe('{"a":1}');
  });
});

describe('the SSE reader', () => {
  it('splits partial chunks and skips malformed events', () => {
    const first = sseEvents('data: {"type":"RUN_STARTED"}\n\ndata: {"ty');
    expect(first.events).toEqual([{ type: 'RUN_STARTED' }]);
    const second = sseEvents(first.rest + 'pe":"RUN_ERROR"}\r\n\r\ndata: not json\n\n');
    expect(second.events).toEqual([{ type: 'RUN_ERROR' }]);
  });
});
