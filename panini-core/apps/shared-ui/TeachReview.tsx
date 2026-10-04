/** Plain-language review of every normalized step. No source code is executed or hidden. */
export function TeachReview({ preview }: { preview: Record<string, unknown> | undefined }) {
  const replay = preview?.replay as any;
  if (!replay || !Array.isArray(replay.steps) || !Array.isArray(replay.expect))
    return <p>Review data is unavailable. Decline and record again.</p>;
  const effects = new Map(
    (Array.isArray(preview?.effects) ? preview.effects : []).map((e: any) => [e.id, e.effect]),
  );
  const artifact = (id: unknown) =>
    typeof id === 'string' &&
    /^\d{13}[a-f0-9]{12}$/.test(id) &&
    /^https?:$/.test(window.location.protocol)
      ? `/api/harness/artifacts/${id}`
      : null;
  return (
    <div aria-label="Review demonstration skill" style={{ display: 'grid', gap: 14 }}>
      <p>{String(preview?.description ?? '')}</p>
      <p className="pc-hint">
        Saving this version grants no permission. Replay applies your current permissions to each
        action and checks the expected result.
      </p>
      <ol style={{ display: 'grid', gap: 10, paddingLeft: 24 }}>
        {replay.steps.map((step: any) => {
          const image = artifact(replay.recording_artifacts?.[step.id]);
          return (
            <li key={step.id}>
              <strong>{String(step.tool).replace('browser_', '').replaceAll('_', ' ')}</strong>
              <span className="pc-hint"> · {String(effects.get(step.id) ?? 'observe')}</span>
              {step.target && (
                <p>
                  {step.target.role} {step.target.name || step.target.name_regex}
                  {step.target.frame && ` · Frame: ${step.target.frame}`}
                </p>
              )}
              {Object.entries(step.args ?? {}).map(([key, value]) => (
                <p key={key}>
                  <span className="pc-hint">{key}: </span>
                  {typeof value === 'string' ? value : JSON.stringify(value)}
                </p>
              ))}
              {image ? (
                <details>
                  <summary>Recorded screenshot</summary>
                  <img
                    loading="lazy"
                    src={image}
                    alt={`Recorded step ${step.id}`}
                    style={{ maxWidth: '100%', maxHeight: 320, objectFit: 'contain', marginTop: 8 }}
                  />
                </details>
              ) : (
                <p className="pc-hint">No recorded screenshot for this step.</p>
              )}
            </li>
          );
        })}
      </ol>
      {Object.keys(replay.inputs ?? {}).length > 0 && (
        <div>
          <strong>Inputs and defaults</strong>
          <dl>
            {Object.entries(replay.inputs).map(([name, input]: [string, any]) => (
              <div key={name} style={{ marginTop: 8 }}>
                <dt>
                  {name} · {input.kind}
                </dt>
                <dd>{input.default || '(provide at replay)'}</dd>
              </div>
            ))}
          </dl>
        </div>
      )}
      <div>
        <strong>Expected results</strong>
        <ul>
          {replay.expect.map((step: any) => (
            <li key={step.id}>
              {step.tool.replace('browser_verify_', '').replaceAll('_', ' ')}:{' '}
              {Object.values(step.args ?? {})
                .map((v) => (typeof v === 'string' ? v : JSON.stringify(v)))
                .join(' · ')}{' '}
              {step.target?.name}
            </li>
          ))}
        </ul>
      </div>
      <details>
        <summary>Full version data</summary>
        <pre className="pc-preview">{JSON.stringify(preview, null, 2)}</pre>
      </details>
    </div>
  );
}
