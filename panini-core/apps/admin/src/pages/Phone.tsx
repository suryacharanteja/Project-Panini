import { useState } from 'react';
import { OutboundPanel } from './OutboundPanel';
import { SaveToSpace } from './spaces/SaveToSpace';
import { api, harness, useResource } from '../api';
import { Badge, Button, Card, Empty, Field, inputClass, Page, useAction } from '../components/ui';

type PhoneStatus = {
  enabled: boolean;
  active: string | null;
  setup: string;
  callers: { number: string; name: string; kind: string; persona_id: string; enabled: boolean }[];
  calls: {
    id: string;
    state: string;
    verified: boolean;
    number: string;
    duration_s: number;
    reason: string;
    receipt_id?: string;
    summary?: string;
  }[];
};

export function PhonePage() {
  const resource = useResource<PhoneStatus>(harness('/phone'), 3000);
  const { busy, run } = useAction();
  const [number, setNumber] = useState('');
  const [name, setName] = useState('');
  const [pin, setPin] = useState('');
  const [kind, setKind] = useState('owner');
  const [persona, setPersona] = useState('panini');
  const [secret, setSecret] = useState('');
  const [hookPath, setHookPath] = useState('');
  const act = (key: string, path: string, body: unknown = {}) =>
    run(key, async () => {
      const result = await api(harness(path), { method: 'POST', body });
      resource.reload();
      return result;
    });
  const data = resource.data;
  return (
    <Page title="Phone line" eyebrow="Talk to PANINI from an ordinary phone">
      {!data ? (
        <Empty>Loading phone setup…</Empty>
      ) : (
        <div className="grid gap-4">
          <OutboundPanel />
          <Card
            title={
              <span>
                Inbound calls{' '}
                <Badge tone={data.enabled ? 'ok' : 'neutral'}>
                  {data.enabled ? 'enabled' : 'off'}
                </Badge>
              </span>
            }
          >
            <p>{data.setup}</p>
            <p className="mt-2 text-sm text-faint">
              Enable PANINI_PHONE_LINE in Settings after configuring the number and webhook. Caller
              ID alone never authorizes a call. Keypad PINs or phone confirmation verify callers;
              actions require their own approvals.
            </p>
          </Card>
          <Card title="Signed webhook">
            <form
              className="grid gap-3"
              onSubmit={(event) => {
                event.preventDefault();
                void run('hook', async () => {
                  const result = await api(harness('/phone/webhook'), {
                    method: 'POST',
                    body: { secret },
                  });
                  setSecret('');
                  setHookPath(result.path);
                });
              }}
            >
              <Field label="OpenAI webhook signing secret">
                <input
                  className={inputClass}
                  aria-label="Webhook signing secret"
                  type="password"
                  autoComplete="off"
                  value={secret}
                  onChange={(e) => setSecret(e.target.value)}
                  required
                />
              </Field>
              <Button type="submit" busy={busy === 'hook'}>
                Save webhook
              </Button>
              {hookPath && (
                <p className="break-all text-sm">
                  Webhook path: {hookPath}. Add it to your public HTTPS ingress origin in the OpenAI
                  project; subscribe to live.transport.incoming.
                </p>
              )}
              <small>
                The signing secret is encrypted for your Windows account. PANINI does not record
                call audio.
              </small>
            </form>
          </Card>
          <Card title="Add or update a caller">
            <form
              className="grid gap-3"
              onSubmit={(event) => {
                event.preventDefault();
                void run('caller', async () => {
                  await api(harness('/phone/callers'), {
                    method: 'POST',
                    body: {
                      number,
                      name,
                      pin,
                      kind,
                      persona_id: kind === 'owner' ? 'panini' : persona,
                    },
                  });
                  setPin('');
                  resource.reload();
                });
              }}
            >
              <Field label="International number">
                <input
                  className={inputClass}
                  aria-label="Caller number"
                  placeholder="+14155550123"
                  value={number}
                  onChange={(e) => setNumber(e.target.value)}
                  required
                />
              </Field>
              <Field label="Name">
                <input
                  className={inputClass}
                  aria-label="Caller name"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  required
                />
              </Field>
              <Field label="Caller type">
                <select
                  className={inputClass}
                  value={kind}
                  onChange={(e) => setKind(e.target.value)}
                >
                  <option value="owner">Owner</option>
                  <option value="contact">Contact with restricted specialist</option>
                  <option value="blocked">Blocked</option>
                </select>
              </Field>
              {kind === 'contact' && (
                <Field label="Specialist ID">
                  <input
                    className={inputClass}
                    value={persona}
                    onChange={(e) => setPersona(e.target.value)}
                    required
                  />
                </Field>
              )}
              {kind !== 'blocked' && (
                <Field label="6–8 digit keypad PIN">
                  <input
                    className={inputClass}
                    aria-label="Caller PIN"
                    type="password"
                    inputMode="numeric"
                    autoComplete="new-password"
                    pattern="[0-9]{6,8}"
                    value={pin}
                    onChange={(e) => setPin(e.target.value)}
                    required
                  />
                </Field>
              )}
              <Button type="submit" busy={busy === 'caller'}>
                Save caller
              </Button>
            </form>
          </Card>
          <Card title="Callers">
            {data.callers.map((c) => (
              <div
                key={c.number}
                className="flex flex-wrap items-center justify-between gap-3 py-2"
              >
                <span>
                  {c.name} · {c.number} · {c.kind} · {c.enabled ? 'enabled' : 'removed'}
                </span>
                <Button
                  disabled={!c.enabled}
                  onClick={() => act(c.number, '/phone/callers/remove', { number: c.number })}
                >
                  Remove
                </Button>
              </div>
            ))}
          </Card>
          {data.active && (
            <Card title="Active call">
              <p>{data.active}</p>
              <div className="mt-3 flex gap-2">
                <Button onClick={() => act('verify', '/phone/verify')}>
                  Send caller confirmation
                </Button>
                <Button onClick={() => act('end', '/phone/end')}>End call</Button>
              </div>
              <small>Confirm the caller in Inbox only when you are on this call.</small>
            </Card>
          )}
          <Card title="Call history">
            {data.calls.length === 0 ? (
              <p className="text-faint">
                No calls yet. Live phone qualification requires a configured SIP number.
              </p>
            ) : (
              data.calls.map((c) => (
                <div key={c.id} className="py-2">
                  <Badge tone={c.state === 'failed' ? 'error' : 'neutral'}>{c.state}</Badge>{' '}
                  {c.number} · {Math.round(c.duration_s)} seconds ·{' '}
                  {c.verified ? 'verified' : 'unverified'}
                  <p>{c.reason}</p>
                  {c.summary && <p className="text-sm text-faint">{c.summary}</p>}
                  {c.receipt_id && <p className="text-xs text-faint">Receipt {c.receipt_id}</p>}
                  {c.receipt_id && <SaveToSpace kind="receipt" sourceId={c.receipt_id} />}
                </div>
              ))
            )}
          </Card>
        </div>
      )}
    </Page>
  );
}
