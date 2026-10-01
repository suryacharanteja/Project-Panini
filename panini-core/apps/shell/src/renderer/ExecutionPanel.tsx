import React, { useEffect, useState } from 'react';

export function ExecutionPanel({
  activity,
  phase,
  outcome,
  mode,
  setMode,
  stop,
  openTerminal,
  connected,
}: any) {
  const [now, setNow] = useState(Date.now());
  const [copied, setCopied] = useState('');
  const active = !['completed', 'blocked', 'failed', 'stopped', 'suspended', 'ready'].includes(
    phase,
  );
  const [ended, setEnded] = useState<number | null>(null);
  useEffect(() => {
    if (!active) {
      setEnded(Date.now());
      return;
    }
    setEnded(null);
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [active, activity.started_at]);
  const seconds = Math.max(
    0,
    Math.floor(
      ((activity.finished_at ? activity.finished_at * 1000 : ended || now) -
        activity.started_at * 1000) /
        1000,
    ),
  );
  const stale = active && now - activity.updated_at * 1000 > 15000;
  const [autoExpanded, setAutoExpanded] = useState(false);
  useEffect(() => {
    setAutoExpanded(false);
  }, [activity.started_at]);
  useEffect(() => {
    if ((seconds >= 15 && active) || ['blocked', 'failed', 'awaiting_confirmation'].includes(phase))
      setAutoExpanded(true);
  }, [seconds >= 15, active, phase]);
  const expanded = mode !== 'compact' || autoExpanded;
  const label =
    phase === 'awaiting_confirmation'
      ? 'Waiting for your approval'
      : phase === 'stopping'
        ? 'Stopping…'
        : phase === 'suspended'
          ? 'Connection interrupted'
          : phase === 'completed'
            ? outcome === 'verified_complete'
              ? 'Verified complete'
              : 'Finished · review result'
            : phase === 'blocked' && outcome === 'partial'
              ? 'Finished · verification pending'
              : phase === 'blocked' || phase === 'failed'
                ? 'Needs attention'
                : phase === 'stopped'
                  ? 'Stopped'
                  : 'Running';
  const items = activity.items || [];
  return (
    <section className="execution" aria-label="PANINI-CLI live execution">
      <div className="execution-heading">
        <img className="execution-icon" src="/brand/panini-cli.png" alt="" width={40} height={40} />
        <div>
          <span className="eyebrow">PANINI-CLI</span>
          <strong role="status">{label}</strong>
        </div>
        <span className="execution-timer" aria-label="Elapsed time">
          {Math.floor(seconds / 60)}:{String(seconds % 60).padStart(2, '0')}
        </span>
      </div>
      <p className="execution-workspace" title={activity.workspace}>
        {activity.workspace}
      </p>
      <div className="execution-meta">
        <span>{activity.model}</span>
        <span>
          {stale ? 'Waiting for the next update…' : active ? 'Live activity' : 'Activity retained'}
        </span>
      </div>
      <div className="execution-toolbar">
        <button
          aria-expanded={expanded}
          onClick={() => {
            setAutoExpanded(false);
            setMode(expanded ? 'compact' : 'expanded');
          }}
        >
          {expanded ? 'Hide details' : 'Show details'}
        </button>
        <button onClick={openTerminal}>Open PowerShell viewer ↗</button>
        {active && (
          <button className="stop" disabled={!connected || phase === 'stopping'} onClick={stop}>
            Stop execution
          </button>
        )}
      </div>
      {expanded && (
        <>
          <div className="execution-output" role="region" aria-label="Command output" tabIndex={0}>
            {!items.length && <p>Starting the coding runtime. Activity will appear here.</p>}
            {items.map((row: any) => (
              <article key={row.id}>
                <div className="execution-item-label">
                  {row.kind === 'commandExecution'
                    ? 'Command'
                    : row.kind === 'fileChange'
                      ? 'File changes'
                      : 'Agent update'}{' '}
                  · {row.status}
                  {row.exit_code != null ? ` · exit ${row.exit_code}` : ''}
                </div>
                {row.command && <pre className="execution-command">{row.command}</pre>}
                {row.paths?.map((path: string) => (
                  <div key={path}>{path}</div>
                ))}
                {row.output && <pre>{row.output}</pre>}
              </article>
            ))}
          </div>
          <div className="execution-note">
            <span>Recent output only. Activity is not independent verification.</span>
            <button
              onClick={async () => {
                try {
                  await window.panini.invoke('execution-copy');
                  setCopied('Copied');
                } catch {
                  setCopied('Copy unavailable');
                }
              }}
            >
              {copied || 'Copy output'}
            </button>
          </div>
        </>
      )}
    </section>
  );
}
