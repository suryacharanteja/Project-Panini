import { h, action, toast } from './ui.mjs';

export function callReport(app, identifier, report) {
  const panel = h(
    'section.card',
    { 'aria-label': 'Call interpretation' },
    h('h3', { text: `Call interpretation · ${report.state}` }),
  );
  if (report.reason) panel.append(h('p', { text: report.reason }));
  if (report.payload) {
    const data = report.payload;
    panel.append(
      h('p', {
        text: `Suggested result: ${data.suggested_status} · ${data.review_decision ? `Owner ${data.review_decision}` : 'Awaiting owner assessment'}`,
      }),
      h('p.muted', { text: data.notice }),
    );
    for (const finding of data.findings) {
      panel.append(h('p', { text: `${finding.kind}: ${finding.text}` }));
      for (const citation of finding.citations)
        panel.append(
          h('blockquote', {
            text: `Callee quote · utterance ${citation.utterance_id}: ${citation.quote}`,
          }),
        );
    }
    if (data.follow_ups.length)
      panel.append(
        h('p.muted', {
          text: `Suggestions only; nothing executed: ${data.follow_ups.map((item) => item.replaceAll('_', ' ')).join('; ')}.`,
        }),
      );
  }
  if (report.state === 'ready')
    panel.append(
      h(
        'button.wide',
        {
          disabled: !!report.review_interrupt_id,
          onclick: (event) =>
            action(event.currentTarget, async () => {
              const restored = await app.fabric.request('GET', '/outbound');
              const current = restored.body?.briefs?.find(
                (row) => row.id === identifier,
              )?.outcome_report;
              if (
                restored.status !== 200 ||
                current?.state !== 'ready' ||
                current.source_digest !== report.source_digest ||
                current.receipt_id !== report.receipt_id
              )
                throw new Error('The report changed. Refresh before reviewing.');
              const result = await app.fabric.request(
                'POST',
                `/outbound/${encodeURIComponent(identifier)}/review-report`,
              );
              if (result.status !== 200)
                throw new Error(result.body?.detail || 'The report cannot be reviewed.');
              toast('Report review is waiting in Inbox.');
              app.show('inbox');
            }),
        },
        report.review_interrupt_id ? 'Review waiting in Inbox' : 'Review interpretation in Inbox',
      ),
    );
  return panel;
}
