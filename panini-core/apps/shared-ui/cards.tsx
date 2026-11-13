import { lazy, Suspense, useState } from 'react';
import { CardBoundary } from './boundary';
import { TeachReview } from './TeachReview';
const MarkdownPreview = lazy(() =>
  import('./MarkdownPreview').then((module) => ({ default: module.MarkdownPreview })),
);
import { resultState, STATE_LABEL, toolDetail, toolLabel, toText, type ToolState } from './text';

/** Inline cards for agent conversations (V2-07): tool calls, approvals, questions, choices, take-overs. */

export type ToolCall = {
  id: string;
  name: string;
  args?: unknown;
  output?: unknown;
  state?: ToolState;
};

function Chip({ state }: { state: ToolState }) {
  return <span className={`pc-chip pc-chip-${state}`}>{STATE_LABEL[state]}</span>;
}

function ToolCardBody({ call }: { call: ToolCall }) {
  const state: ToolState =
    call.state || (call.output === undefined ? 'working' : resultState(call.output));
  const output = toText(call.output, 1200);
  return (
    <section
      className="pc-card pc-tool"
      aria-label={`${toolLabel(call.name)}: ${STATE_LABEL[state]}`}
    >
      <header>
        <strong>{toolLabel(call.name)}</strong>
        <Chip state={state} />
      </header>
      {toolDetail(call.args) && <p className="pc-detail">{toolDetail(call.args)}</p>}
      {output && state !== 'working' && (
        <details>
          <summary>Result</summary>
          <pre>{output}</pre>
        </details>
      )}
    </section>
  );
}

export function ToolCard({ call }: { call: ToolCall }) {
  return (
    <CardBoundary label="tool card">
      <ToolCardBody call={call} />
    </CardBoundary>
  );
}

export type InterruptView = {
  id: string;
  kind?: string;
  reason?: string;
  title?: string;
  message?: string;
  preview?: unknown;
  level?: string;
  expiresAt?: string;
};

type Decide = (approved: boolean, allowForSession?: boolean) => Promise<void> | void;

function InterruptCardBody({
  interrupt,
  onDecide,
  onReview,
  allowSession = false,
}: {
  interrupt: InterruptView;
  onDecide: Decide;
  onReview?: (approved: boolean, content: string) => Promise<void> | void;
  allowSession?: boolean;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const preview = interrupt.preview as Record<string, unknown> | undefined;
  const pageDraft =
    interrupt.kind === 'review' &&
    interrupt.reason === 'page_draft' &&
    typeof preview?.content === 'string';
  const teachReview = interrupt.kind === 'review' && interrupt.reason === 'teach_version';
  const detail = pageDraft
    ? String(preview.content)
    : toText(preview?.detail ?? preview?.effect ?? preview, 1500);
  const [reviewContent, setReviewContent] = useState(pageDraft ? String(preview?.content) : '');
  const [rendered, setRendered] = useState(false);
  const permission = preview?.mcp_permission as { tool?: unknown; seconds?: unknown } | undefined;
  const sessionAvailable =
    allowSession &&
    interrupt.reason === 'mcp_effect' &&
    interrupt.kind === 'approval' &&
    interrupt.level === 'tap' &&
    typeof permission?.tool === 'string' &&
    permission.tool.startsWith('browser_') &&
    permission.seconds === 3600;
  const decide = async (approved: boolean, session = false) => {
    setBusy(true);
    setError('');
    try {
      if (pageDraft && onReview) await onReview(approved, reviewContent);
      else await onDecide(approved, session);
    } catch (exc) {
      setError(toText((exc as Error)?.message) || 'The decision could not be sent.');
      setBusy(false);
    }
  };
  const stronger = interrupt.level === 'verify' || interrupt.level === 'local';
  return (
    <section
      className="pc-card pc-interrupt"
      aria-label={`Approval needed: ${toText(interrupt.title, 200)}`}
    >
      <header>
        <strong>{toText(interrupt.title, 200) || 'Approve this step?'}</strong>
        <Chip state="attention" />
      </header>
      <p>{toText(interrupt.message, 1000)}</p>
      {pageDraft && (
        <details onToggle={(event) => setRendered(event.currentTarget.open)}>
          <summary>Rendered draft</summary>
          {rendered && (
            <Suspense fallback={<p role="status">Rendering draft…</p>}>
              <MarkdownPreview content={reviewContent} />
            </Suspense>
          )}
        </details>
      )}
      {pageDraft && typeof preview?.previous_content === 'string' && (
        <details>
          <summary>
            Previous revision{preview.previous_truncated ? ' (first 20,000 characters)' : ''}
          </summary>
          <pre className="pc-preview">{preview.previous_content}</pre>
        </details>
      )}
      {pageDraft && typeof preview?.diff === 'string' && (
        <details>
          <summary>
            Changes{preview.diff_truncated ? ' (shortened; full text is available above)' : ''}
          </summary>
          <pre className="pc-preview">{preview.diff}</pre>
        </details>
      )}
      {teachReview ? (
        <TeachReview preview={preview} />
      ) : pageDraft && onReview ? (
        <textarea
          aria-label="Review page draft"
          value={reviewContent}
          maxLength={20000}
          onChange={(event) => setReviewContent(event.target.value)}
          className="pc-preview w-full min-h-64"
        />
      ) : (
        detail && <pre className="pc-preview">{detail}</pre>
      )}
      <p className="pc-hint">
        Nothing happens until you approve.
        {stronger ? ' This step needs approval on this PC or with a passkey.' : ''}
      </p>
      <div className="pc-actions">
        <button
          type="button"
          className="pc-primary"
          disabled={busy}
          onClick={() => void decide(true)}
        >
          Approve
        </button>
        {sessionAvailable && (
          <button type="button" disabled={busy} onClick={() => void decide(true, true)}>
            Allow this tool for 1 hour
          </button>
        )}
        <button type="button" disabled={busy} onClick={() => void decide(false)}>
          Decline
        </button>
      </div>
      {sessionAvailable && (
        <p className="pc-hint">
          Temporary permission covers this tool’s navigation and input. Submissions still need
          approval.
        </p>
      )}
      {error && (
        <p role="alert" className="pc-error">
          {error}
        </p>
      )}
    </section>
  );
}

export function InterruptCard(props: {
  interrupt: InterruptView;
  onDecide: Decide;
  onReview?: (approved: boolean, content: string) => Promise<void> | void;
  allowSession?: boolean;
}) {
  return (
    <CardBoundary label="approval card">
      <InterruptCardBody {...props} />
    </CardBoundary>
  );
}

function QuestionCardBody({
  question,
  onAnswer,
}: {
  question: InterruptView;
  onAnswer: (text: string) => void;
}) {
  const [draft, setDraft] = useState('');
  return (
    <section className="pc-card pc-question" aria-label="PANINI has a question">
      <header>
        <strong>{toText(question.title, 200) || 'PANINI needs you'}</strong>
      </header>
      <p>{toText(question.message, 1000)}</p>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          if (draft.trim()) onAnswer(draft.trim());
        }}
      >
        <input value={draft} onChange={(e) => setDraft(e.target.value)} aria-label="Your answer" />
        <button type="submit" className="pc-primary" disabled={!draft.trim()}>
          Answer
        </button>
      </form>
    </section>
  );
}

export function QuestionCard(props: { question: InterruptView; onAnswer: (text: string) => void }) {
  return (
    <CardBoundary label="question card">
      <QuestionCardBody {...props} />
    </CardBoundary>
  );
}

export function ChoiceCard({
  title,
  choices,
  onChoose,
}: {
  title: unknown;
  choices: { id: string; label: unknown }[];
  onChoose: (id: string) => void;
}) {
  return (
    <CardBoundary label="choice card">
      <section className="pc-card pc-choice" aria-label="Choose one">
        <header>
          <strong>{toText(title, 200) || 'Choose one'}</strong>
        </header>
        <ol>
          {(Array.isArray(choices) ? choices : []).map((choice, index) => (
            <li key={toText(choice.id) || index}>
              <button type="button" onClick={() => onChoose(toText(choice.id))}>
                {index + 1}. {toText(choice.label, 200)}
              </button>
            </li>
          ))}
        </ol>
      </section>
    </CardBoundary>
  );
}

export function TakeoverCard({ message, onDone }: { message: unknown; onDone: () => void }) {
  return (
    <CardBoundary label="take-over card">
      <section className="pc-card pc-takeover" aria-label="Your turn">
        <header>
          <strong>Your turn</strong>
          <Chip state="attention" />
        </header>
        <p>{toText(message, 1000) || 'Finish this step yourself, then press Done.'}</p>
        <div className="pc-actions">
          <button type="button" className="pc-primary" onClick={onDone}>
            Done
          </button>
        </div>
      </section>
    </CardBoundary>
  );
}

export function ReceiptChip({
  receipt,
}: {
  receipt: { id?: unknown; tool?: unknown; effect?: unknown; outcome?: unknown };
}) {
  const id = toText(receipt?.id, 64);
  return (
    <CardBoundary label="receipt">
      <a className="pc-receipt" href={id ? `/receipts?id=${encodeURIComponent(id)}` : undefined}>
        {toolLabel(receipt?.tool)} · {toText(receipt?.effect, 40)} · {toText(receipt?.outcome, 40)}
      </a>
    </CardBoundary>
  );
}
