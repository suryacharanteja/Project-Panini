import { useState } from 'react';
import { api, harness, useResource } from '../api';
import { Badge, Button, Card, Empty, inputClass, Page, useAction } from '../components/ui';

/** Remote access (V2-14): the red-team gate, transport state, pairing with a short code, devices and push. */
type Device = {
  id: string;
  name: string;
  kind: string;
  status: string;
  created: number;
  last_seen?: number;
  sas?: string;
};
type Fabric = {
  mode: string;
  state: string;
  reason: string;
  origin: string;
  pc_id: string;
  gate: { passed: boolean; reason: string; deferred?: boolean };
  devices: Device[];
  hooks: string[];
};

const TONE: Record<string, 'ok' | 'warn' | 'neutral' | 'error'> = {
  ready: 'ok',
  blocked: 'warn',
  needs_setup: 'warn',
  off: 'neutral',
};

export function RemotePage() {
  const fabric = useResource<Fabric>(harness('/fabric'), 3000);
  const { busy, run } = useAction();
  const [code, setCode] = useState<{ code: string; url: string } | null>(null);
  const [relay, setRelay] = useState('');
  const data = fabric.data;
  const act = (key: string, path: string, method = 'POST', body: unknown = {}) =>
    run(key, async () => {
      const result = await api(harness(path), { method, body });
      fabric.reload();
      return result;
    });

  return (
    <Page title="Remote access" deva="दूर" eyebrow="Your phone, from anywhere">
      {!data ? (
        <Empty>Loading remote access…</Empty>
      ) : (
        <div className="grid gap-4">
          <Card
            title={
              <span className="flex items-center gap-2">
                Status{' '}
                <Badge tone={TONE[data.state] ?? 'neutral'}>{data.state.replace('_', ' ')}</Badge>
                <Badge tone="neutral">{data.mode}</Badge>
              </span>
            }
            actions={
              <Button busy={busy === 'start'} onClick={() => act('start', '/fabric/start')}>
                Apply
              </Button>
            }
          >
            {data.gate.deferred && (
              <p className="text-sm text-faint">
                Red-team qualification: future development. Remote access follows your configured
                mode.
              </p>
            )}
            {!data.gate.passed && !data.gate.deferred && (
              <p className="text-sm text-slate">
                Remote access stays off until the red-team gate passes: {data.gate.reason}
              </p>
            )}
            {data.reason && (data.gate.passed || data.gate.deferred) && (
              <p className="text-sm text-slate">{data.reason}</p>
            )}
            {data.origin && (
              <p className="text-sm text-faint">Phones reach PANINI at {data.origin}</p>
            )}
            <p className="mt-2 text-xs text-faint">
              Mode is PANINI_REMOTE_MODE (off, lan, tailscale or relay) in Settings. PC id{' '}
              {data.pc_id}.
            </p>
            <form
              className="mt-3 flex gap-2"
              onSubmit={(event) => {
                event.preventDefault();
                void act('relay', '/fabric/settings', 'PATCH', { relay_url: relay });
              }}
            >
              <input
                className={inputClass}
                value={relay}
                onChange={(e) => setRelay(e.target.value)}
                placeholder="Relay address (https://…), for relay mode"
                aria-label="Relay address"
              />
              <Button type="submit" disabled={!relay}>
                Save
              </Button>
            </form>
          </Card>
          <Card
            title="Pair a phone"
            actions={
              <Button
                tone="primary"
                disabled={data.state !== 'ready'}
                busy={busy === 'code'}
                onClick={() =>
                  run('code', async () =>
                    setCode(await api(harness('/fabric/code'), { method: 'POST', body: {} })),
                  )
                }
              >
                New pairing code
              </Button>
            }
          >
            {code ? (
              <div className="grid gap-1 text-sm">
                <p>
                  Open this on your phone (valid 5 minutes):{' '}
                  <span className="mono break-all">{code.url}</span>
                </p>
                <p className="text-faint">
                  Then confirm below that the six digits match on both screens.
                </p>
              </div>
            ) : (
              <p className="text-sm text-faint">
                Start remote access, then make a code to pair a phone.
              </p>
            )}
          </Card>
          <Card title="Devices">
            {data.devices.length === 0 ? (
              <p className="text-sm text-faint">No phones paired yet.</p>
            ) : (
              <ul className="grid gap-2 text-sm">
                {data.devices.map((d) => (
                  <li key={d.id} className="flex items-center gap-2">
                    <strong>{d.name}</strong>
                    <Badge
                      tone={
                        d.status === 'active' ? 'ok' : d.status === 'pending' ? 'warn' : 'neutral'
                      }
                    >
                      {d.status}
                    </Badge>
                    {d.status === 'pending' && (
                      <>
                        <span className="mono">{d.sas}</span>
                        <Button onClick={() => act(`c-${d.id}`, `/fabric/devices/${d.id}/confirm`)}>
                          The digits match
                        </Button>
                      </>
                    )}
                    {d.status !== 'revoked' && (
                      <Button onClick={() => act(`r-${d.id}`, `/fabric/devices/${d.id}`, 'DELETE')}>
                        Revoke
                      </Button>
                    )}
                  </li>
                ))}
              </ul>
            )}
            <div className="mt-3">
              <Button busy={busy === 'push'} onClick={() => act('push', '/fabric/push-test')}>
                Send a test notification
              </Button>
            </div>
          </Card>
        </div>
      )}
    </Page>
  );
}
