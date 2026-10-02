import { ShieldCheck } from 'lucide-react';
import { useState } from 'react';
import { harness, useResource } from '../api';
import { Badge, Card, Empty, inputClass, Page } from '../components/ui';

type Receipt = {
  id: string;
  run_id: string;
  tool: string;
  capability: string;
  effect: string;
  outcome: string;
  dispatched: number;
  started_at: number;
  args_view: Record<string, unknown>;
  result_view: string | null;
  decision: { action: string; level: string; reasons: string[] } | null;
  evidence: { kind: string; summary: string }[];
  entries?: { seq: number; kind: string; hash: string }[];
};

const OUTCOME_TONE: Record<string, 'ok' | 'warn' | 'error' | 'neutral'> = {
  ok: 'ok',
  pending: 'neutral',
  blocked: 'warn',
  cancelled: 'warn',
  failed: 'error',
  unknown_effect: 'error',
};

/** Every tool call PANINI made: decision, effect, outcome, evidence — hash-chained (V2-04). */
export function ReceiptsPage() {
  const [query, setQuery] = useState('');
  const [open, setOpen] = useState<string | null>(null);
  const list = useResource<{ receipts: Receipt[] }>(
    harness(`/receipts?limit=200${query ? `&q=${encodeURIComponent(query)}` : ''}`),
    10000,
  );
  const verify = useResource<{ ok: boolean; entries: number; first_bad_seq: number | null }>(
    harness('/ledger/verify'),
    60000,
  );
  const detail = useResource<Receipt>(open ? harness(`/receipts/${open}`) : null);

  return (
    <Page title="Receipts" deva="प्रमाण" eyebrow="Ledger">
      <Card
        title={
          <span className="flex items-center gap-2">
            <ShieldCheck size={16} />
            {verify.data
              ? verify.data.ok
                ? `Chain verifies (${verify.data.entries} entries)`
                : `Chain broken at entry ${verify.data.first_bad_seq}`
              : 'Checking the chain…'}
          </span>
        }
      >
        <input
          className={inputClass}
          placeholder="Filter by tool, target or result…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          aria-label="Filter receipts"
        />
        {list.data && list.data.receipts.length === 0 && (
          <Empty>No receipts yet. Turn on the ledger in Settings → System → PANINI v2.</Empty>
        )}
        <ul className="mt-3 grid gap-1">
          {list.data?.receipts.map((r) => (
            <li key={r.id}>
              <button
                type="button"
                onClick={() => setOpen(open === r.id ? null : r.id)}
                className="flex w-full flex-wrap items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm hover:bg-zinc-3"
                aria-expanded={open === r.id}
              >
                <span className="mono w-36 text-xs text-faint">
                  {new Date(r.started_at * 1000).toLocaleString()}
                </span>
                <span className="mono text-slate">{r.tool}</span>
                <Badge>{r.effect}</Badge>
                <Badge tone={OUTCOME_TONE[r.outcome] ?? 'neutral'}>{r.outcome}</Badge>
                {r.decision && r.decision.action !== 'allow' && (
                  <Badge tone="copper">
                    {r.decision.action} · {r.decision.level}
                  </Badge>
                )}
                {r.dispatched ? <span className="text-xs text-faint">dispatched</span> : null}
              </button>
              {open === r.id && detail.data && (
                <div className="mb-2 ml-2 grid gap-2 rounded-md border border-line p-3 text-xs">
                  <div className="text-faint">Run {detail.data.run_id}</div>
                  <pre className="mono overflow-x-auto whitespace-pre-wrap text-muted">
                    {JSON.stringify(detail.data.args_view, null, 2)}
                  </pre>
                  {detail.data.decision?.reasons?.length ? (
                    <div>Why: {detail.data.decision.reasons.join('; ')}</div>
                  ) : null}
                  {detail.data.result_view && (
                    <div className="text-muted">{detail.data.result_view}</div>
                  )}
                  {detail.data.evidence.map((e, i) => (
                    <div key={i}>
                      Evidence: <Badge tone="ok">{e.kind}</Badge> {e.summary}
                    </div>
                  ))}
                  <div className="mono text-faint">
                    {detail.data.entries
                      ?.map((e) => `${e.seq}:${e.kind}:${e.hash.slice(0, 8)}`)
                      .join('  ')}
                  </div>
                </div>
              )}
            </li>
          ))}
        </ul>
      </Card>
    </Page>
  );
}
