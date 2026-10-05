import { useEffect, useState } from 'react';
import { api, harness, useResource } from '../api';
import { Button, Card, inputClass } from '../components/ui';

const labels: Record<string, string> = {
  tap: 'Tap approval',
  verify: 'Verified approval',
  local: 'PC approval',
  result: 'Foreground result',
  background: 'Background result or watch',
};
const routes = ['avatar', 'push', 'channel', 'inbox', 'toast', 'after_call'];

export function PresencePanel() {
  const resource = useResource<any>(harness('/presence'), 3000);
  const [draft, setDraft] = useState<any>(null);
  const [kind, setKind] = useState('tap');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  useEffect(() => {
    if (resource.data && !draft) setDraft(resource.data.settings);
  }, [resource.data, draft]);
  if (!draft)
    return (
      <Card title="Presence and continuity">
        <p role="status">{resource.error || 'Loading continuity settings…'}</p>
      </Card>
    );
  return (
    <Card title="Presence and continuity">
      <p className="text-sm text-muted">
        {resource.data?.enabled
          ? `Current presence: ${resource.data.state.replaceAll('_', ' ')}`
          : 'Continuity is off. Enable PANINI_PRESENCE in Runtime to use these rules.'}
      </p>
      <p className="mt-2 text-sm text-muted">
        Push acceptance confirms delivery to a push service. It does not confirm that a phone
        displayed it. Urgent owner calls need a separate PC approval and an acknowledged outbound
        trunk. Live SIP delivery is not qualified.
      </p>
      <div className="mt-3 rounded-lg border border-zinc-3 p-3 text-sm">
        <strong>Urgent responsibility failures</strong>
        <p className="mt-1 text-muted">
          If approved, PANINI may call your configured owner number after an urgent item remains
          unresolved for five minutes while you are away. The grant lasts seven days, allows at most
          one call per day and three total, and uses a fixed two-minute brief with no voicemail.
          Current call policy and calling hours still apply.
        </p>
        <p className="mt-2" role="status">
          {resource.data?.owner_call_escalation?.status === 'active'
            ? `Active for ${resource.data.owner_call_escalation.number} until ${new Date(resource.data.owner_call_escalation.expires * 1000).toLocaleString()}`
            : resource.data?.owner_call_escalation?.status === 'proposed'
              ? 'Waiting for your PC approval in Inbox.'
              : resource.data?.owner_call_escalation?.status === 'unavailable'
                ? 'Grant unavailable: check the trunk, flags, expiry and current policy.'
                : 'Off'}
        </p>
        <div className="mt-2 flex gap-2">
          {resource.data?.owner_call_escalation?.status === 'off' ? (
            <Button
              type="button"
              disabled={busy}
              onClick={async () => {
                setBusy(true);
                setMessage('');
                try {
                  await api(harness('/presence/urgent-grants'), { method: 'POST' });
                  setMessage('Review the exact urgent call grant in Inbox on this PC.');
                  await resource.reload();
                } catch (error) {
                  setMessage((error as Error).message);
                } finally {
                  setBusy(false);
                }
              }}
            >
              Review urgent call opt-in
            </Button>
          ) : (
            <Button
              type="button"
              disabled={busy}
              onClick={async () => {
                setBusy(true);
                setMessage('');
                try {
                  await api(
                    harness(`/presence/urgent-grants/${resource.data.owner_call_escalation.id}`),
                    { method: 'DELETE' },
                  );
                  setMessage('Urgent owner calling revoked.');
                  await resource.reload();
                } catch (error) {
                  setMessage((error as Error).message);
                } finally {
                  setBusy(false);
                }
              }}
            >
              Revoke urgent calls
            </Button>
          )}
        </div>
      </div>
      <form
        className="mt-4 grid gap-4"
        onSubmit={async (event) => {
          event.preventDefault();
          setBusy(true);
          setMessage('');
          try {
            await api(harness('/presence/settings'), { method: 'PUT', body: draft });
            setMessage('Continuity rules saved.');
            await resource.reload();
          } catch (error) {
            setMessage((error as Error).message);
          } finally {
            setBusy(false);
          }
        }}
      >
        <label className="text-sm">
          Notification type
          <select
            aria-label="Notification type"
            className={inputClass}
            value={kind}
            onChange={(event) => setKind(event.target.value)}
          >
            {Object.entries(labels).map(([value, text]) => (
              <option key={value} value={value}>
                {text}
              </option>
            ))}
          </select>
        </label>
        {Object.entries(draft.rules[kind]).map(([state, values]) => (
          <fieldset key={state} className="rounded-lg border border-zinc-3 p-3">
            <legend className="px-1 text-sm">{state.replaceAll('_', ' ')}</legend>
            <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
              {routes.map((route) => {
                const entries = values as [string, number][];
                const entry = entries.find(([name]) => name === route);
                function update(next: [string, number][]) {
                  setDraft({
                    ...draft,
                    rules: { ...draft.rules, [kind]: { ...draft.rules[kind], [state]: next } },
                  });
                }
                return (
                  <div key={route} className="flex items-center gap-2">
                    <label className="flex items-center gap-2 text-sm">
                      <input
                        type="checkbox"
                        checked={Boolean(entry)}
                        onChange={(event) =>
                          update(
                            event.target.checked
                              ? [...entries, [route, 0]]
                              : entries.filter(([name]) => name !== route),
                          )
                        }
                      />
                      {route.replaceAll('_', ' ')}
                    </label>
                    {entry && (
                      <input
                        type="number"
                        className={inputClass + ' max-w-24'}
                        aria-label={`${state} ${route} delay seconds`}
                        min={0}
                        max={86400}
                        value={entry[1]}
                        onChange={(event) =>
                          update(
                            entries.map(([name, delay]) => [
                              name,
                              name === route ? Number(event.target.value) : delay,
                            ]),
                          )
                        }
                      />
                    )}
                  </div>
                );
              })}
            </div>
          </fieldset>
        ))}
        <p className="text-xs text-muted">
          Delays are in seconds from the original event. Viewing an approval never makes its
          decision.
        </p>
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={draft.quiet.enabled}
            onChange={(event) =>
              setDraft({ ...draft, quiet: { ...draft.quiet, enabled: event.target.checked } })
            }
          />
          Quiet hours for push and channel notifications
        </label>
        <div className="grid gap-3 sm:grid-cols-3">
          {(['start', 'end'] as const).map((key) => (
            <label key={key} className="text-sm">
              Quiet {key} hour
              <input
                type="number"
                className={inputClass}
                min={0}
                max={23}
                value={draft.quiet[key]}
                onChange={(event) =>
                  setDraft({
                    ...draft,
                    quiet: { ...draft.quiet, [key]: Number(event.target.value) },
                  })
                }
              />
            </label>
          ))}
          <label className="text-sm">
            Timezone
            <input
              className={inputClass}
              value={draft.quiet.timezone}
              placeholder="local or Asia/Dubai"
              onChange={(event) =>
                setDraft({ ...draft, quiet: { ...draft.quiet, timezone: event.target.value } })
              }
            />
          </label>
        </div>
        <label className="text-sm">
          Preferred private owner channel
          <select
            className={inputClass}
            aria-label="Preferred private owner channel"
            value={draft.preferred_channel ? JSON.stringify(draft.preferred_channel) : ''}
            onChange={(event) =>
              setDraft({
                ...draft,
                preferred_channel: event.target.value ? JSON.parse(event.target.value) : null,
              })
            }
          >
            <option value="">None configured</option>
            {(resource.data?.channel_destinations || []).map((destination: any) => (
              <option key={JSON.stringify(destination)} value={JSON.stringify(destination)}>
                {destination.account_id} · {destination.thread_key}
              </option>
            ))}
          </select>
        </label>
        <div>
          <Button type="submit" disabled={busy}>
            {busy ? 'Saving…' : 'Save continuity rules'}
          </Button>
        </div>
        {message && (
          <p role="status" className="text-sm">
            {message}
          </p>
        )}
      </form>
    </Card>
  );
}
