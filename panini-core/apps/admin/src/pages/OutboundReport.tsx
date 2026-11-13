import { Button } from '../components/ui';

export type CallReport = {
  state: string;
  reason?: string;
  receipt_id?: string;
  review_interrupt_id?: string;
  payload?: {
    suggested_status: string;
    review_decision: string | null;
    notice: string;
    findings: {
      kind: string;
      text: string;
      citations: { utterance_id: number; quote: string }[];
    }[];
    follow_ups: string[];
  };
};

export function OutboundReport({ report, review }: { report: CallReport; review: () => void }) {
  return (
    <section aria-label="Call interpretation" className="mt-4 rounded-lg border border-line p-3">
      <h4 className="font-semibold">Call interpretation · {report.state}</h4>
      {report.reason && <p className="mt-2 text-sm">{report.reason}</p>}
      {report.payload && (
        <>
          <p className="mt-2 text-sm">
            Suggested result: {report.payload.suggested_status} ·{' '}
            {report.payload.review_decision
              ? `Owner ${report.payload.review_decision}`
              : 'Awaiting owner assessment'}
          </p>
          <p className="mt-2 text-xs text-faint">{report.payload.notice}</p>
          {report.payload.findings.map((finding, index) => (
            <div key={index} className="mt-3 text-sm">
              <p className="break-words">
                {finding.kind}: {finding.text}
              </p>
              {finding.citations.map((citation, quoteIndex) => (
                <blockquote
                  key={quoteIndex}
                  className="mt-2 break-words border-l-2 border-line pl-3"
                >
                  Callee quote · utterance {citation.utterance_id}: {citation.quote}
                </blockquote>
              ))}
            </div>
          ))}
          {!!report.payload.follow_ups.length && (
            <p className="mt-3 text-sm">
              Suggestions only; nothing executed:{' '}
              {report.payload.follow_ups.map((item) => item.replaceAll('_', ' ')).join('; ')}.
            </p>
          )}
        </>
      )}
      {report.state === 'ready' && (
        <Button onClick={review} disabled={!!report.review_interrupt_id}>
          {report.review_interrupt_id
            ? 'Review waiting in Inbox'
            : 'Review interpretation in Inbox'}
        </Button>
      )}
      {report.receipt_id && (
        <p className="mt-2 text-xs text-faint">Interpretation receipt {report.receipt_id}</p>
      )}
    </section>
  );
}
