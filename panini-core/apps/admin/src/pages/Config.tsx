import { Link, useParams } from '@tanstack/react-router';
import { ExternalLink, Plus, RefreshCw, RotateCcw, Trash2, X } from 'lucide-react';
import { useEffect, useState } from 'react';
import { admin, ago, api, harness, useResource, when } from '../api';
import { SettingsForm } from '../components/SettingsForm';
import {
  Badge,
  Button,
  Card,
  Empty,
  Field,
  inputClass,
  Page,
  StateDot,
  useAction,
} from '../components/ui';

const CAPABILITY: Record<string, { title: string; deva: string; groups: string[]; text: string }> =
  {
    voice: {
      title: 'Voice',
      deva: 'वाक्',
      groups: ['voice'],
      text: 'How PANINI listens and speaks in live conversation.',
    },
    browser: {
      title: 'Browser',
      deva: 'जाल',
      groups: ['browser'],
      text: 'PANINI works in your own Chrome tab through the JevRev Claw extension. It never silently opens another browser.',
    },
    search: {
      title: 'Search',
      deva: 'अन्वेषण',
      groups: [],
      text: 'Ranked, sourced web research. It needs the OpenAI and AI Gateway keys.',
    },
    desktop: {
      title: 'Desktop',
      deva: 'पटल',
      groups: ['desktop'],
      text: 'Operate supported Windows applications, asking before every action.',
    },
    code: {
      title: 'Code',
      deva: 'सूत्र',
      groups: ['code'],
      text: 'Write, run and test code in the folders you approve. Every change and command asks first.',
    },
  };

export function CapabilityPage() {
  const { name } = useParams({ strict: false }) as { name: string };
  const cap = CAPABILITY[name] ?? CAPABILITY.voice!;
  const health = useResource<any>(admin('/health'));
  const check = (health.data?.checks ?? []).find((c: any) => c.id === `capability:${name}`);
  return (
    <Page title={cap.title} deva={cap.deva} eyebrow="Capability">
      <div className="grid gap-5">
        <Card>
          <div className="flex flex-wrap items-center gap-3">
            <StateDot state={check?.state === 'ok' ? 'ok' : 'idle'} />
            <span className="text-sm">
              {check ? check.detail : name === 'voice' ? 'Uses the OpenAI key' : 'Checking…'}
            </span>
          </div>
          <p className="mt-2 max-w-3xl text-sm text-muted">{cap.text}</p>
        </Card>
        {cap.groups.length > 0 && <SettingsForm groups={cap.groups} title="Settings" />}
        {name === 'browser' && <ExtensionCard />}
        {name === 'search' && (
          <Card title="Keys" deva="कुञ्जी">
            <p className="text-sm text-muted">
              Search uses the OpenAI and AI Gateway keys. Manage them in{' '}
              <Link to="/settings/keys" className="text-copper-light">
                Keys &amp; models
              </Link>
              , or try Search in the{' '}
              <Link to="/playground" className="text-copper-light">
                Playground
              </Link>
              .
            </p>
          </Card>
        )}
        {name === 'code' && (
          <>
            <DockerCard health={health.data} reload={() => health.setData(null as any)} />
            <HooksCard />
          </>
        )}
      </div>
    </Page>
  );
}

function DockerCard({ health }: { health: any; reload: () => void }) {
  const docker = (health?.checks ?? []).find((c: any) => c.id === 'docker');
  return (
    <Card title="Docker sandbox" deva="रक्षा">
      <div className="flex items-center gap-3">
        <StateDot
          state={docker?.state === 'ok' ? 'ok' : docker?.state === 'error' ? 'error' : 'idle'}
        />
        <span className="text-sm">{docker?.detail ?? 'Checking…'}</span>
      </div>
      <p className="mt-2 text-sm text-muted">
        With the sandbox set to <code className="mono text-copper-glow">docker</code>, every command
        runs in a Linux container with no network, no extra privileges and only the workspace
        mounted. Start Docker Desktop first.
      </p>
    </Card>
  );
}

function HooksCard() {
  const hooks = useResource<any>(admin('/hooks'));
  const [draft, setDraft] = useState<Record<string, { matcher: string; command: string }[]> | null>(
    null,
  );
  const { busy, run } = useAction();
  useEffect(() => {
    if (hooks.data && !draft) setDraft(hooks.data.hooks ?? {});
  }, [hooks.data, draft]);
  if (!hooks.data || !draft) return null;
  const events: string[] = hooks.data.events;
  return (
    <Card
      title="Hooks"
      deva="अङ्कुश"
      actions={
        <Button
          tone="primary"
          busy={busy === 'save'}
          onClick={() =>
            run(
              'save',
              async () => {
                const r = await api<any>(admin('/hooks'), {
                  method: 'PUT',
                  body: { hooks: draft },
                });
                setDraft(r.hooks);
              },
              'Hooks saved.',
            )
          }
        >
          Save hooks
        </Button>
      }
    >
      <p className="-mt-2 mb-4 text-sm text-muted">
        Your own commands that run around PANINI-CLI&apos;s work. A <b>PreToolUse</b> hook that
        exits with code 2 blocks the action. Project hooks in a repository still need trusting with{' '}
        <code className="mono">panini-cli hooks --trust</code>.
      </p>
      <div className="grid gap-4">
        {events.map((event) => (
          <div key={event}>
            <div className="mono mb-2 text-xs text-copper-light">{event}</div>
            <div className="grid gap-2">
              {(draft[event] ?? []).map((h, i) => (
                <div key={i} className="flex gap-2">
                  {(event === 'PreToolUse' || event === 'PostToolUse') && (
                    <input
                      className={inputClass + ' mono max-w-52 text-xs'}
                      placeholder="tool pattern, e.g. run_command"
                      value={h.matcher}
                      onChange={(e) =>
                        setDraft({
                          ...draft,
                          [event]: draft[event]!.map((x, j) =>
                            j === i ? { ...x, matcher: e.target.value } : x,
                          ),
                        })
                      }
                    />
                  )}
                  <input
                    className={inputClass + ' mono text-xs'}
                    placeholder="command"
                    value={h.command}
                    onChange={(e) =>
                      setDraft({
                        ...draft,
                        [event]: draft[event]!.map((x, j) =>
                          j === i ? { ...x, command: e.target.value } : x,
                        ),
                      })
                    }
                  />
                  <Button
                    tone="ghost"
                    aria-label="Remove hook"
                    onClick={() =>
                      setDraft({ ...draft, [event]: draft[event]!.filter((_, j) => j !== i) })
                    }
                  >
                    <X size={14} />
                  </Button>
                </div>
              ))}
              <div>
                <Button
                  onClick={() =>
                    setDraft({
                      ...draft,
                      [event]: [...(draft[event] ?? []), { matcher: '', command: '' }],
                    })
                  }
                >
                  <Plus size={13} /> Add
                </Button>
              </div>
            </div>
          </div>
        ))}
      </div>
    </Card>
  );
}

function ExtensionCard() {
  const devices = useResource<any>('/api/extension/devices');
  const status = useResource<any>('/api/browser/extension/status', 5000);
  const [code, setCode] = useState<any>(null);
  const { busy, run } = useAction();
  const s = status.data ?? {};
  return (
    <Card
      title="JevRev Claw (Chrome extension)"
      deva="सेतु"
      actions={
        <Button
          tone="primary"
          busy={busy === 'code'}
          onClick={() =>
            run('code', async () =>
              setCode(await api('/api/extension/pairing-code', { method: 'POST' })),
            )
          }
        >
          Create pairing code
        </Button>
      }
    >
      <div className="flex flex-wrap items-center gap-3 text-sm">
        <StateDot
          state={
            s.connected && s.attached_tab ? 'ok' : s.connected || s.paired ? 'warning' : 'idle'
          }
        />
        {s.connected
          ? s.attached_tab
            ? `Connected · ${s.tab_title || 'tab attached'}`
            : 'Connected, no tab attached'
          : s.paired
            ? 'Paired, not connected'
            : 'Not connected'}
        {s.version && <Badge>v{s.version}</Badge>}
      </div>
      {code && (
        <div className="mono mt-3 rounded-lg border border-amber/30 bg-amber/5 p-3 text-amber">
          Enter this code in the extension: <b className="text-lg tracking-widest">{code.code}</b>
          {code.expires_at && (
            <span className="ml-2 text-xs">
              (valid until {new Date(code.expires_at * 1000).toLocaleTimeString()})
            </span>
          )}
        </div>
      )}
      <ul className="derive mt-4 grid gap-2">
        {(devices.data?.devices ?? []).map((d: any) => (
          <li
            key={d.device_id ?? d.id}
            className="flex items-center gap-3 rounded-lg border border-line bg-obsidian/50 px-3 py-2.5 text-sm"
          >
            <span className="flex-1">{d.label || d.origin || 'Chrome'}</span>
            <span className="mono text-[11px] text-faint">{d.created ? ago(d.created) : ''}</span>
            <Button
              tone="ghost"
              onClick={() =>
                run(
                  'rm',
                  async () => {
                    await api(`/api/extension/devices/${d.device_id ?? d.id}`, {
                      method: 'DELETE',
                    });
                    await devices.reload();
                  },
                  'Device removed.',
                )
              }
            >
              <Trash2 size={13} />
            </Button>
          </li>
        ))}
      </ul>
    </Card>
  );
}

export function KeysPage() {
  return (
    <Page title="Keys & models" deva="कुञ्जी" eyebrow="Settings">
      <div className="grid gap-5">
        <SettingsForm
          groups={['keys']}
          title="API keys"
          deva="रहस्य"
          description="Keys are write-only: they are never shown again, and a new OpenAI or AssemblyAI key is tested before it replaces the current one."
        />
        <SettingsForm
          groups={['models']}
          title="Models"
          deva="प्रतिरूप"
          description="Which model each part of PANINI uses. Leave a field empty to use the default."
        />
      </div>
    </Page>
  );
}

export function DevicesPage() {
  const phone = useResource<any>(harness('/mobile'), 10000);
  const [code, setCode] = useState<any>(null);
  const { busy, run } = useAction();
  const p = phone.data;
  return (
    <Page title="Devices" deva="यन्त्र" eyebrow="Settings">
      <div className="grid gap-5">
        <ExtensionCard />
        <Card
          title="Phone companion"
          deva="दूरवाणी"
          actions={
            p && (
              <Button
                tone={p.enabled ? 'quiet' : 'primary'}
                busy={busy === 'toggle'}
                onClick={() =>
                  run('toggle', async () => {
                    phone.setData(
                      await api(harness(p.enabled ? '/mobile/disable' : '/mobile/enable'), {
                        method: 'POST',
                      }),
                    );
                    setCode(null);
                  })
                }
              >
                {p.enabled ? 'Turn off' : 'Turn on'}
              </Button>
            )
          }
        >
          <p className="-mt-2 mb-4 text-sm text-muted">
            See the inbox, approve decisions and send requests from your phone on the same Wi-Fi.
            Only devices on your home network with a pairing code can connect. Traffic is not
            encrypted on the network, so use it only on Wi-Fi you trust.
          </p>
          {p?.error && <p className="mb-3 text-sm text-danger">{p.error}</p>}
          {p?.enabled && (
            <>
              <p className="text-sm">
                Open on your phone:{' '}
                <span className="mono text-copper-glow">
                  {p.urls.join('  or  ') || 'no home-network address found'}
                </span>
              </p>
              <div className="mt-3">
                <Button
                  busy={busy === 'code'}
                  onClick={() =>
                    run('code', async () =>
                      setCode(await api(harness('/mobile/code'), { method: 'POST' })),
                    )
                  }
                >
                  Create pairing code
                </Button>
              </div>
              {code && (
                <div className="mono mt-3 rounded-lg border border-amber/30 bg-amber/5 p-3 text-amber">
                  Pairing code <b className="tracking-widest">{code.code}</b> · valid until{' '}
                  {new Date(code.expires * 1000).toLocaleTimeString()} · one use
                </div>
              )}
            </>
          )}
          <ul className="derive mt-4 grid gap-2">
            {(p?.devices ?? []).map((d: any) => (
              <li
                key={d.id}
                className="flex items-center gap-3 rounded-lg border border-line bg-obsidian/50 px-3 py-2.5 text-sm"
              >
                <span className="flex-1">{d.name}</span>
                <span className="mono text-[11px] text-faint">
                  {d.last_seen ? `seen ${ago(d.last_seen)}` : `paired ${ago(d.created)}`}
                </span>
                <Button
                  tone="ghost"
                  onClick={() =>
                    run(
                      'rm' + d.id,
                      async () => {
                        await api(harness(`/mobile/devices/${d.id}`), { method: 'DELETE' });
                        await phone.reload();
                      },
                      'Phone removed.',
                    )
                  }
                >
                  <Trash2 size={13} />
                </Button>
              </li>
            ))}
          </ul>
        </Card>
      </div>
    </Page>
  );
}

export function SystemPage() {
  const system = useResource<any>(admin('/system'));
  return (
    <Page title="System" deva="विन्यास" eyebrow="Settings">
      <div className="grid gap-5">
        <SettingsForm
          groups={['agent', 'network']}
          title="Runtime"
          description="Settings marked “applies after restart” take effect the next time PANINI starts (tray → Quit, then open PANINI)."
        />
        <Card title="About" deva="परिचय">
          <dl className="mono grid gap-2 text-xs sm:grid-cols-[180px_1fr]">
            {system.data &&
              Object.entries(system.data).map(([k, v]) => (
                <div key={k} className="contents">
                  <dt className="text-faint">{k.replace(/_/g, ' ')}</dt>
                  <dd className="break-all text-slate/85">{String(v)}</dd>
                </div>
              ))}
          </dl>
        </Card>
      </div>
    </Page>
  );
}

export function PlaygroundPage() {
  const items = [
    {
      href: '/playground/compare',
      title: 'Compare',
      deva: 'तुलना',
      text: 'Ask several models the same question side by side.',
    },
    {
      href: '/search',
      title: 'Search',
      deva: 'अन्वेषण',
      text: 'Web research with ranked, sourced results.',
    },
    {
      href: '/browser',
      title: 'Browser',
      deva: 'जाल',
      text: 'Run and watch browser tasks in your Chrome tab or a separate browser.',
    },
    {
      href: '/activity',
      title: 'Live activity',
      deva: 'दर्शन',
      text: 'Watch what the avatar is doing right now: steps, results and evidence.',
    },
  ];
  return (
    <Page title="Playground" deva="प्रयोग" eyebrow="Try a capability directly">
      <div className="derive grid gap-4 sm:grid-cols-2">
        {items.map((i) => (
          <a
            key={i.href}
            href={i.href}
            className="surface group relative overflow-hidden p-6 transition hover:-translate-y-0.5 hover:border-copper/60"
          >
            <div className="deva pointer-events-none absolute -right-3 -top-4 text-6xl text-copper/[0.07]">
              {i.deva}
            </div>
            <div className="flex items-center gap-2 text-lg font-light">
              {i.title} <ExternalLink size={14} className="text-faint group-hover:text-copper" />
            </div>
            <p className="mt-1 text-sm text-muted">{i.text}</p>
          </a>
        ))}
      </div>
    </Page>
  );
}

export function AuditPage() {
  const audit = useResource<any>(admin('/audit'), 15000);
  const { busy, run } = useAction();
  return (
    <Page
      title="Audit log"
      deva="लेखा"
      eyebrow="Every change, and who made it"
      actions={
        <Button busy={busy === 'r'} onClick={() => run('r', () => audit.reload())}>
          <RefreshCw size={14} /> Refresh
        </Button>
      }
    >
      <Card>
        {(audit.data?.entries ?? []).length === 0 ? (
          <Empty>No changes yet. Everything you change in PANINI Admin is recorded here.</Empty>
        ) : (
          <ol className="derive grid gap-2">
            {audit.data.entries.map((e: any) => (
              <li key={e.id} className="rounded-lg border border-line bg-obsidian/50 px-3.5 py-3">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge tone="copper">{e.kind}</Badge>
                  <Badge>{e.action}</Badge>
                  <span className="text-sm">{e.summary}</span>
                  <span className="mono ml-auto text-[11px] text-faint">
                    {e.actor} · {when(e.at)}
                  </span>
                </div>
                {e.kind === 'settings' && Object.keys(e.diff ?? {}).length > 0 && (
                  <div className="mono mt-2 grid gap-1 text-[11px]">
                    {Object.entries(e.diff).map(([k, v]: any) => (
                      <div key={k} className="text-muted">
                        {k}:{' '}
                        {v.secret ? (
                          <span className="text-faint">changed (secret)</span>
                        ) : (
                          <>
                            <span className="text-danger/80 line-through">
                              {String(v.old ?? '∅')}
                            </span>{' '}
                            → <span className="text-gold">{String(v.new ?? '∅')}</span>
                          </>
                        )}
                      </div>
                    ))}
                  </div>
                )}
                {e.kind === 'settings' &&
                  e.action === 'update' &&
                  Object.values(e.diff ?? {}).some((v: any) => !v.secret) && (
                    <div className="mt-2">
                      <Button
                        tone="ghost"
                        busy={busy === e.id}
                        onClick={() =>
                          run(
                            e.id,
                            async () => {
                              await api(admin(`/audit/${e.id}/restore`), { method: 'POST' });
                              await audit.reload();
                            },
                            'Restored the previous values.',
                          )
                        }
                      >
                        <RotateCcw size={13} /> Restore previous values
                      </Button>
                    </div>
                  )}
              </li>
            ))}
          </ol>
        )}
      </Card>
    </Page>
  );
}

export function NotFound() {
  return (
    <Page title="Not found" deva="अज्ञात">
      <Card>
        <p className="text-sm text-muted">
          This page does not exist.{' '}
          <Link to="/" className="text-copper-light">
            Go home
          </Link>
          .
        </p>
      </Card>
    </Page>
  );
}

const SURFACES: Record<string, string> = {
  route: 'Routing (Jev)',
  planner: 'Routing (LLM planner)',
  'browser.plan': 'Browser task plan (LLM)',
  'browser.propose': 'Browser proposals (LLM)',
  'browser.decide': 'Browser decisions (Jev)',
  'browser.blocked': 'Browser blocked checks (Jev)',
  'browser.page_select': 'Browser page selection (Jev)',
  'desktop.decide': 'Desktop decisions (Jev)',
  'desktop.visual_check': 'Desktop visual checks',
  'agent.finish': 'Agent success checks (Jev)',
  'agent.select': 'Agent context selection (Jev)',
  'agent.done': 'Agent early finish (Jev)',
  agent: 'Agent (LLM tool loop)',
  voice: 'Voice turns (LLM)',
};

const tokens = (n: number) =>
  n >= 1e6 ? `${(n / 1e6).toFixed(2)}M` : n >= 1e3 ? `${(n / 1e3).toFixed(1)}k` : String(n);

export function UsagePage() {
  const usage = useResource<any>(admin('/usage'), 10000);
  const rows = Object.entries<any>(usage.data?.surfaces ?? {}).sort(
    (a, b) => b[1].cost - a[1].cost,
  );
  const total = usage.data?.total;
  const jevCalls = rows
    .filter(([, s]) => s.models.includes('typesafe-ai/jev'))
    .reduce((n, [, s]) => n + s.calls, 0);
  return (
    <Page
      title="Usage & cost"
      deva="व्यय"
      eyebrow="Settings"
      actions={
        <Button tone="quiet" onClick={() => usage.reload()}>
          <RefreshCw size={14} /> Refresh
        </Button>
      }
    >
      <div className="grid gap-5">
        <Card title="Since PANINI started">
          <p className="mb-4 text-sm text-slate/70">
            Jev makes the decisions and bills input tokens only; LLMs propose actions and write
            text. Cached input is billed at a lower rate.
          </p>
          <dl className="mono grid grid-cols-2 gap-3 text-xs sm:grid-cols-4">
            {[
              ['Calls', total?.calls ?? 0],
              ['Jev decisions', jevCalls],
              ['Input tokens', tokens(total?.input_tokens ?? 0)],
              ['Estimated cost', `$${(total?.cost ?? 0).toFixed(4)}`],
            ].map(([k, v]) => (
              <div key={String(k)}>
                <dt className="text-faint">{k}</dt>
                <dd className="text-lg text-slate">{v}</dd>
              </div>
            ))}
          </dl>
        </Card>
        <Card title="By surface">
          {rows.length === 0 ? (
            <Empty>No model calls yet. Run a task and they appear here.</Empty>
          ) : (
            <div className="overflow-x-auto">
              <table className="mono w-full text-left text-xs">
                <thead className="text-faint">
                  <tr>
                    <th className="py-2 pr-3 font-normal">Surface</th>
                    <th className="py-2 pr-3 font-normal">Calls</th>
                    <th className="py-2 pr-3 font-normal">Input</th>
                    <th className="py-2 pr-3 font-normal">Cached</th>
                    <th className="py-2 pr-3 font-normal">Output</th>
                    <th className="py-2 pr-3 font-normal">Cost</th>
                    <th className="py-2 font-normal">Models</th>
                  </tr>
                </thead>
                <tbody className="text-slate/85">
                  {rows.map(([name, s]) => (
                    <tr key={name} className="border-t border-white/5">
                      <td className="py-2 pr-3">{SURFACES[name] ?? name}</td>
                      <td className="py-2 pr-3">{s.calls}</td>
                      <td className="py-2 pr-3">{tokens(s.input_tokens)}</td>
                      <td className="py-2 pr-3">{tokens(s.cached_tokens)}</td>
                      <td className="py-2 pr-3">{tokens(s.output_tokens)}</td>
                      <td className="py-2 pr-3">${s.cost.toFixed(4)}</td>
                      <td className="py-2 text-faint">{s.models.join(', ')}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </div>
    </Page>
  );
}

export function LearningPage() {
  const identity = useResource<any>(admin('/identity'));
  const learning = useResource<any>(admin('/learning'), 15000);
  const { busy, run } = useAction();
  const [text, setText] = useState<string | null>(null);
  const value = text ?? identity.data?.text ?? '';
  const lessons: any[] = learning.data?.lessons ?? [];
  const active = lessons.filter((l) => l.status === 'active');
  const past = lessons.filter((l) => l.status !== 'active');
  return (
    <Page title="Identity & learning" deva="आत्मबोध" eyebrow="Settings">
      <div className="grid gap-5">
        <Card title="Who PANINI is" deva="परिचय">
          <p className="mb-3 text-sm text-slate/70">
            PANINI's identity opens every conversation: its name, who built it and how it behaves.
          </p>
          <textarea
            className={`${inputClass} mono min-h-[260px] text-xs`}
            value={value}
            onChange={(e) => setText(e.target.value)}
            aria-label="PANINI identity"
          />
          <div className="mt-3 flex gap-2">
            <Button
              tone="primary"
              busy={busy === 'identity'}
              onClick={() =>
                void run(
                  'identity',
                  async () => {
                    await api(admin('/identity'), { method: 'PUT', body: { text: value } });
                    setText(null);
                    await identity.reload();
                  },
                  'Identity saved.',
                )
              }
            >
              Save identity
            </Button>
            <Button
              tone="quiet"
              onClick={() =>
                void run(
                  'reset',
                  async () => {
                    await api(admin('/identity'), { method: 'PUT', body: { text: '' } });
                    setText(null);
                    await identity.reload();
                  },
                  'Back to the built-in identity.',
                )
              }
            >
              Reset to default
            </Button>
          </div>
        </Card>
        <Card title="How PANINI is doing" deva="मूल्यांकन">
          <dl className="mono grid grid-cols-2 gap-3 text-xs sm:grid-cols-4">
            {[
              ['Tasks graded by Jev', learning.data?.tasks_graded ?? 0],
              ['Average grade', `${Math.round((learning.data?.average_grade ?? 0) * 100)}%`],
              ['Praise', learning.data?.feedback?.praise ?? 0],
              ['Corrections', learning.data?.feedback?.correction ?? 0],
            ].map(([k, v]) => (
              <div key={String(k)}>
                <dt className="text-faint">{k}</dt>
                <dd className="text-lg text-slate">{v}</dd>
              </div>
            ))}
          </dl>
        </Card>
        <Card title="Lessons PANINI learned" deva="शिक्षा">
          <p className="mb-3 text-sm text-slate/70">
            Learned from your praise and corrections. Jev checks each lesson before it goes live,
            and lessons that make results worse are retired automatically. Remove any you disagree
            with.
          </p>
          {active.length === 0 ? (
            <Empty>No lessons yet. Praise or correct PANINI and it learns.</Empty>
          ) : (
            <ul className="grid gap-2">
              {active.map((lesson) => (
                <li key={lesson.id} className="surface flex items-start justify-between gap-3 p-3">
                  <div className="min-w-0">
                    <div className="text-sm text-slate">{lesson.text}</div>
                    <div className="mono mt-1 text-[11px] text-faint">
                      From {lesson.origin} · used {lesson.uses}×
                      {lesson.uses
                        ? ` · ${Math.round((lesson.score_sum / lesson.uses) * 100)}% grade`
                        : ''}
                      {lesson.evidence ? ` · “${String(lesson.evidence).slice(0, 80)}”` : ''}
                    </div>
                  </div>
                  <Button
                    tone="ghost"
                    aria-label="Remove lesson"
                    onClick={() =>
                      void run(
                        lesson.id,
                        async () => {
                          await api(admin(`/learning/lessons/${lesson.id}`), { method: 'DELETE' });
                          await learning.reload();
                        },
                        'Lesson removed.',
                      )
                    }
                  >
                    <Trash2 size={14} />
                  </Button>
                </li>
              ))}
            </ul>
          )}
          {past.length > 0 && (
            <p className="mono mt-3 text-[11px] text-faint">
              {past.length} earlier lesson{past.length === 1 ? '' : 's'} retired or removed.
            </p>
          )}
        </Card>
        <Card title="Remembered automatically" deva="स्मृति">
          {(learning.data?.auto_memories ?? []).length === 0 ? (
            <Empty>Nothing yet. Tell PANINI about yourself and it remembers what matters.</Empty>
          ) : (
            <ul className="grid gap-1 text-sm text-slate/85">
              {learning.data.auto_memories.map((m: any) => (
                <li key={m.id}>• {m.text}</li>
              ))}
            </ul>
          )}
          <p className="mt-3 text-xs text-faint">Edit or delete memories on the Memory page.</p>
        </Card>
      </div>
    </Page>
  );
}
