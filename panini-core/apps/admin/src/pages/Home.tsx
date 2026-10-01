import { Link } from '@tanstack/react-router';
import { ArrowRight, Plug, RefreshCw, Smartphone, Sparkles, Timer } from 'lucide-react';
import { admin, ago, api, harness, useResource } from '../api';
import { Badge, Button, Card, cx, StateDot, useAction } from '../components/ui';
import { Genesis } from '../genesis/Genesis';

const FIX: Record<string, string> = {
  keys: '/settings/keys',
  devices: '/settings/devices',
  code: '/capabilities/code',
  desktop: '/capabilities/desktop',
  browser: '/capabilities/browser',
  search: '/capabilities/search',
  agent: '/settings/system',
};

const CAPS = [
  { id: 'search', label: 'Search', deva: 'अन्वेषण', text: 'Ranked, sourced web research.' },
  { id: 'browser', label: 'Browser', deva: 'जाल', text: 'Your own Chrome tab, via JevRev Claw.' },
  { id: 'desktop', label: 'Desktop', deva: 'पटल', text: 'Supported Windows applications.' },
  { id: 'code', label: 'Code', deva: 'सूत्र', text: 'Writes, runs and tests in approved folders.' },
];

export function Home() {
  const health = useResource<any>(admin('/health'), 60000);
  const overview = useResource<any>(admin('/overview'), 10000);
  const reviews = useResource<any>(harness('/reviews'), 15000);
  const notices = useResource<any>(harness('/notices'), 15000);
  const { busy, run } = useAction();

  const checks: any[] = health.data?.checks ?? [];
  const status = overview.data?.status;
  const ready = checks
    .filter((c) => c.id.startsWith('capability:') && c.state === 'ok')
    .map((c) => c.id.split(':')[1]);
  const attention = [
    ...(reviews.data?.reviews ?? []),
    ...(notices.data?.notices ?? []).filter((n: any) => n.status === 'unread'),
  ];
  const today = overview.data?.today ?? {};
  const problems = checks.filter((c) => c.state === 'error');

  return (
    <div>
      <section className="relative isolate h-[min(74vh,720px)] min-h-[460px] overflow-hidden border-b border-line">
        <Genesis
          activity={status?.running ? 'working' : 'idle'}
          ready={ready}
          className="absolute inset-0 h-full w-full"
        />
        <div className="pointer-events-none absolute inset-0 bg-gradient-to-b from-obsidian/10 via-transparent to-obsidian" />
        <div className="pointer-events-none absolute inset-x-0 top-0 flex justify-between px-6 pt-6 md:px-10">
          <div>
            <div className="mono text-[11px] uppercase tracking-[0.3em] text-copper-light/80">
              The PANINI Genesis
            </div>
            <div className="deva mt-1 text-sm text-copper/45">अष्टाध्यायी · सूत्र → वृत्ति</div>
          </div>
          <div className="mono text-right text-[11px] leading-5 text-faint">
            <div>DHATU · intent</div>
            <div>SUTRA · gateway :3200</div>
            <div>VRITTI · delegation</div>
            <div>ANUVRITTI · memory</div>
          </div>
        </div>
        <div className="pointer-events-none absolute inset-x-0 bottom-0 px-6 pb-8 md:px-10">
          <h1 className="display-font text-5xl font-light tracking-[0.12em] md:text-7xl">
            <span className="copper-text">PANINI</span>
          </h1>
          <p className="mt-2 max-w-xl text-base font-light text-slate/80">
            Unstructured intent, compiled into verified action. Configure everything it knows, can
            do and is allowed to do, from here.
          </p>
          <div className="mono mt-4 flex flex-wrap items-center gap-3 text-xs text-muted">
            <StateDot
              state={
                status?.running
                  ? 'running'
                  : problems.length
                    ? 'error'
                    : health.data
                      ? 'ok'
                      : 'idle'
              }
            />
            {status?.running
              ? `Working: ${status.current?.title ?? 'a task'}`
              : problems.length
                ? `${problems.length} problem${problems.length > 1 ? 's' : ''} need attention`
                : health.data
                  ? 'Idle · every system ready'
                  : 'Checking the system…'}
          </div>
        </div>
      </section>

      <div className="mx-auto grid w-full max-w-6xl gap-5 px-5 py-8 md:px-8 lg:grid-cols-3">
        <Card
          className="lg:col-span-2"
          title="System health"
          deva="आरोग्य"
          actions={
            <Button
              busy={busy === 'health'}
              onClick={() =>
                run(
                  'health',
                  async () => health.setData(await api(admin('/health?refresh=true'))),
                  'Checked again.',
                )
              }
            >
              <RefreshCw size={14} /> Check now
            </Button>
          }
        >
          {!health.data && !health.error && (
            <p className="text-sm text-faint">Probing each dependency…</p>
          )}
          {health.error && <p className="text-sm text-danger">{health.error}</p>}
          <ul className="derive grid gap-2 sm:grid-cols-2">
            {checks.map((c) => (
              <li
                key={c.id}
                className={cx(
                  'flex items-start gap-3 rounded-xl border bg-obsidian/50 px-3.5 py-3',
                  c.state === 'error'
                    ? 'border-danger/40'
                    : c.state === 'warning'
                      ? 'border-amber/25'
                      : 'border-line',
                )}
              >
                <span className="mt-1.5">
                  <StateDot state={c.state} />
                </span>
                <div className="min-w-0 flex-1">
                  <div className="text-sm">{c.label}</div>
                  <div className="truncate text-xs text-faint" title={c.detail}>
                    {c.detail}
                  </div>
                </div>
                {(c.state === 'error' || c.state === 'warning') && FIX[c.fix] && (
                  <Link
                    to={FIX[c.fix]}
                    className="mono shrink-0 text-xs text-copper-light hover:text-copper-glow"
                  >
                    Fix →
                  </Link>
                )}
              </li>
            ))}
          </ul>
          {health.data && (
            <p className="mono mt-3 text-[11px] text-faint">Checked {ago(health.data.checked)}</p>
          )}
        </Card>

        <Card
          title="Needs you"
          deva="प्रतीक्षा"
          actions={
            <Link to="/inbox" className="text-xs text-copper-light">
              Inbox →
            </Link>
          }
        >
          {attention.length === 0 ? (
            <p className="text-sm text-faint">Nothing is waiting for you.</p>
          ) : (
            <ul className="derive grid gap-2">
              {attention.slice(0, 5).map((a: any) => (
                <li key={a.id} className="rounded-lg border border-line bg-obsidian/50 px-3 py-2.5">
                  <div className="flex items-center gap-2">
                    <Badge tone={a.kind === 'approval' || a.task_id ? 'warn' : 'copper'}>
                      {a.kind}
                    </Badge>
                    <span className="truncate text-sm">{a.title ?? a.message?.split('\n')[0]}</span>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card title="Today" deva="अद्य">
          <dl className="derive grid grid-cols-2 gap-3">
            {[
              ['Verified', today.verified_complete ?? 0],
              ['Partial', today.partial ?? 0],
              ['Failed', (today.failed ?? 0) + (today.blocked ?? 0)],
              ['Evidence', today.evidence ?? 0],
              ['Memories', status?.memories ?? 0],
              ['Watches', status?.watches ?? 0],
            ].map(([label, value]) => (
              <div
                key={label as string}
                className="rounded-lg border border-line bg-obsidian/50 px-3 py-2"
              >
                <dt className="mono text-[10px] uppercase tracking-widest text-faint">{label}</dt>
                <dd className="mt-1 text-2xl font-light text-copper-glow">{value as number}</dd>
              </div>
            ))}
          </dl>
          <p className="mono mt-3 text-[11px] text-faint">Background runs in the last 24 hours.</p>
        </Card>

        <Card className="lg:col-span-2" title="Capabilities" deva="वृत्ति">
          <div className="derive grid gap-3 sm:grid-cols-2">
            {CAPS.map((cap) => {
              const check = checks.find((c) => c.id === `capability:${cap.id}`);
              return (
                <Link
                  key={cap.id}
                  to={`/capabilities/${cap.id}`}
                  className={cx(
                    'group relative overflow-hidden rounded-xl border bg-obsidian/50 p-4 transition hover:-translate-y-0.5',
                    check?.state === 'ok'
                      ? 'border-copper/40 hover:border-copper'
                      : 'border-line hover:border-copper/50',
                  )}
                >
                  <div className="deva pointer-events-none absolute -right-2 -top-3 text-5xl text-copper/[0.07]">
                    {cap.deva}
                  </div>
                  <div className="flex items-center gap-2">
                    <StateDot state={check?.state === 'ok' ? 'ok' : 'idle'} />
                    <span className="font-medium">{cap.label}</span>
                    <span className="deva text-xs text-copper/40">{cap.deva}</span>
                  </div>
                  <p className="mt-1.5 text-xs text-muted">{cap.text}</p>
                  <p className="mono mt-2 truncate text-[11px] text-faint">
                    {check?.detail ?? '—'}
                  </p>
                </Link>
              );
            })}
          </div>
        </Card>

        <Card className="lg:col-span-3" title="Quick actions" deva="आरम्भ">
          <div className="derive grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {[
              {
                to: '/build/connectors',
                icon: <Plug size={16} />,
                label: 'Add a connector',
                text: 'Give PANINI a new tool.',
              },
              {
                to: '/build/skills',
                icon: <Sparkles size={16} />,
                label: 'Create a skill',
                text: 'Teach a repeatable procedure.',
              },
              {
                to: '/build/automations',
                icon: <Timer size={16} />,
                label: 'New automation',
                text: 'Schedule or watch something.',
              },
              {
                to: '/settings/devices',
                icon: <Smartphone size={16} />,
                label: 'Pair a device',
                text: 'Chrome extension or phone.',
              },
            ].map((a) => (
              <Link
                key={a.to + a.label}
                to={a.to}
                className="group flex items-start gap-3 rounded-xl border border-line bg-obsidian/50 p-4 transition hover:border-copper/60"
              >
                <span className="mt-0.5 text-copper">{a.icon}</span>
                <span className="flex-1">
                  <span className="block text-sm">{a.label}</span>
                  <span className="block text-xs text-faint">{a.text}</span>
                </span>
                <ArrowRight
                  size={14}
                  className="mt-1 text-faint transition group-hover:translate-x-0.5 group-hover:text-copper"
                />
              </Link>
            ))}
          </div>
        </Card>
      </div>
    </div>
  );
}
