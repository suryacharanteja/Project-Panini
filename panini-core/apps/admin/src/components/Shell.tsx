import { Link, Outlet, useNavigate, useRouterState } from '@tanstack/react-router';
import {
  Activity,
  Bot,
  Box,
  Boxes,
  Brain,
  Code2,
  Command,
  FileClock,
  FlaskConical,
  Gauge,
  Globe,
  Home,
  Inbox,
  KeyRound,
  Layers,
  MessagesSquare,
  Mic,
  Monitor,
  Plug,
  Radar,
  Search,
  Settings2,
  ShieldCheck,
  Smartphone,
  Sparkles,
  Timer,
  Eye,
} from 'lucide-react';
import { type ReactNode, useEffect, useMemo, useState } from 'react';
import { admin, harness, useResource } from '../api';
import { cx, StateDot } from './ui';

type Item = { to: string; label: string; icon: ReactNode; deva?: string };
type Section = { title?: string; items: Item[] };

export const NAV: Section[] = [
  {
    items: [
      { to: '/', label: 'Home', icon: <Home size={16} />, deva: 'मूल' },
      { to: '/inbox', label: 'Inbox', icon: <Inbox size={16} />, deva: 'सूचना' },
      {
        to: '/conversations',
        label: 'Conversations',
        icon: <MessagesSquare size={16} />,
        deva: 'संवाद',
      },
      { to: '/runs', label: 'Activity', icon: <Activity size={16} />, deva: 'क्रिया' },
      { to: '/receipts', label: 'Receipts', icon: <ShieldCheck size={16} />, deva: 'प्रमाण' },
      { to: '/projects', label: 'Projects', icon: <Boxes size={16} />, deva: 'योजना' },
      { to: '/spaces', label: 'Spaces', icon: <Layers size={16} /> },
    ],
  },
  {
    title: 'Build',
    items: [
      { to: '/build/skills', label: 'Skills', icon: <Sparkles size={16} />, deva: 'कौशल' },
      { to: '/build/connectors', label: 'Connectors', icon: <Plug size={16} />, deva: 'सेतु' },
      { to: '/build/automations', label: 'Automations', icon: <Timer size={16} />, deva: 'नियम' },
      { to: '/build/agents', label: 'Agents', icon: <Bot size={16} />, deva: 'कर्ता' },
      { to: '/build/personas', label: 'Personas', icon: <Bot size={16} /> },
      {
        to: '/build/capabilities',
        label: 'Capabilities',
        icon: <Layers size={16} />,
        deva: 'शक्ति',
      },
      { to: '/build/computers', label: 'Computers', icon: <Box size={16} />, deva: 'यन्त्र' },
      { to: '/build/live', label: 'Live', icon: <Eye size={16} />, deva: 'दृश्य' },
      { to: '/build/safety', label: 'Safety', icon: <ShieldCheck size={16} />, deva: 'रक्षा' },
      { to: '/build/remote', label: 'Remote access', icon: <Smartphone size={16} />, deva: 'दूर' },
      { to: '/build/phone', label: 'Phone line', icon: <Smartphone size={16} /> },
      { to: '/memory', label: 'Memory', icon: <Brain size={16} />, deva: 'स्मृति' },
    ],
  },
  {
    title: 'Capabilities',
    items: [
      { to: '/capabilities/voice', label: 'Voice', icon: <Mic size={16} />, deva: 'वाक्' },
      { to: '/capabilities/browser', label: 'Browser', icon: <Globe size={16} />, deva: 'जाल' },
      { to: '/capabilities/search', label: 'Search', icon: <Search size={16} />, deva: 'अन्वेषण' },
      { to: '/capabilities/desktop', label: 'Desktop', icon: <Monitor size={16} />, deva: 'पटल' },
      { to: '/capabilities/code', label: 'Code', icon: <Code2 size={16} />, deva: 'सूत्र' },
    ],
  },
  {
    title: 'System',
    items: [
      { to: '/playground', label: 'Playground', icon: <FlaskConical size={16} />, deva: 'प्रयोग' },
      { to: '/settings/keys', label: 'Keys & models', icon: <KeyRound size={16} /> },
      { to: '/settings/devices', label: 'Devices', icon: <Smartphone size={16} /> },
      { to: '/settings/learning', label: 'Identity & learning', icon: <Sparkles size={16} /> },
      { to: '/settings/usage', label: 'Usage & cost', icon: <Gauge size={16} /> },
      { to: '/settings/system', label: 'System', icon: <Settings2 size={16} /> },
      { to: '/audit', label: 'Audit log', icon: <FileClock size={16} /> },
    ],
  },
];

function Palette({ onClose }: { onClose: () => void }) {
  const navigate = useNavigate();
  const [query, setQuery] = useState('');
  const [index, setIndex] = useState(0);
  const actions = useMemo(
    () => [
      ...NAV.flatMap((s) => s.items.map((i) => ({ label: `Go to ${i.label}`, to: i.to }))),
      { label: 'Add a connector', to: '/build/connectors' },
      { label: 'Create a skill', to: '/build/skills' },
      { label: 'New automation or watch', to: '/build/automations' },
      { label: 'Change or test the OpenAI key', to: '/settings/keys' },
      { label: 'Turn coding on or off', to: '/capabilities/code' },
      { label: 'Pair the Chrome extension', to: '/settings/devices' },
      { label: 'Pair a phone', to: '/settings/devices' },
      { label: 'Check health', to: '/' },
    ],
    [],
  );
  const matches = actions.filter((a) => a.label.toLowerCase().includes(query.toLowerCase()));
  const go = (to: string) => {
    onClose();
    void navigate({ to });
  };
  return (
    <div
      className="fixed inset-0 z-50 grid place-items-start justify-center bg-obsidian/70 pt-[14vh] backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-label="Command palette"
        className="surface w-[min(560px,92vw)] overflow-hidden p-0"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center gap-2 border-b border-line px-4">
          <Command size={15} className="text-copper" />
          <input
            autoFocus
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setIndex(0);
            }}
            onKeyDown={(e) => {
              if (e.key === 'Escape') onClose();
              if (e.key === 'ArrowDown') setIndex((i) => Math.min(i + 1, matches.length - 1));
              if (e.key === 'ArrowUp') setIndex((i) => Math.max(i - 1, 0));
              if (e.key === 'Enter' && matches[index]) go(matches[index].to);
            }}
            placeholder="What do you want to do?"
            aria-label="Search actions"
            className="h-12 flex-1 bg-transparent text-sm text-slate outline-none placeholder:text-faint"
          />
          <kbd className="mono text-[10px] text-faint">ESC</kbd>
        </div>
        <ul className="max-h-80 overflow-y-auto p-1.5" role="listbox">
          {matches.map((a, i) => (
            <li key={a.label}>
              <button
                type="button"
                role="option"
                aria-selected={i === index}
                onMouseEnter={() => setIndex(i)}
                onClick={() => go(a.to)}
                className={cx(
                  'w-full rounded-md px-3 py-2 text-left text-sm',
                  i === index ? 'bg-copper/15 text-copper-glow' : 'text-slate/85',
                )}
              >
                {a.label}
              </button>
            </li>
          ))}
          {!matches.length && <li className="px-3 py-4 text-sm text-faint">No matching action.</li>}
        </ul>
      </div>
    </div>
  );
}

export function Shell() {
  const path = useRouterState({ select: (s) => s.location.pathname });
  const health = useResource<any>(admin('/health'), 60000);
  const status = useResource<any>(harness('/status'), 8000);
  const overview = useResource<any>(admin('/overview'), 15000);
  const [palette, setPalette] = useState(false);
  const [menu, setMenu] = useState(false);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        setPalette((open) => !open);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);
  useEffect(() => setMenu(false), [path]);

  const signedOut = health.error.includes('Sign in') || status.error.includes('Sign in');
  const issues = (health.data?.checks ?? []).filter(
    (c: any) => c.state === 'error' || c.state === 'warning',
  );
  const s = status.data;
  const inbox = s ? (s.notices ?? 0) + (s.reviews ?? 0) : 0;
  const working = !!s?.running;

  return (
    <div className="flex min-h-screen">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:fixed focus:left-3 focus:top-3 focus:z-50"
      >
        Skip to content
      </a>
      <aside
        className={cx(
          'fixed inset-y-0 left-0 z-40 w-64 shrink-0 overflow-y-auto border-r border-line bg-obsidian/95 px-3 pb-6 backdrop-blur md:sticky md:top-0 md:h-screen md:translate-x-0',
          menu ? 'translate-x-0' : '-translate-x-full',
          'transition-transform',
        )}
        aria-label="PANINI Admin navigation"
      >
        <Link to="/" className="flex items-center gap-3 px-2 py-5">
          <img
            src="/admin/brand/panini-icon-64.png"
            alt=""
            width={34}
            height={34}
            className="rounded-lg"
          />
          <div>
            <div className="copper-text text-lg font-semibold tracking-[0.18em]">PANINI</div>
            <div className="mono text-[10px] uppercase tracking-[0.2em] text-faint">Admin</div>
          </div>
        </Link>
        <nav className="grid gap-5">
          {NAV.map((section, i) => (
            <div key={i}>
              {section.title && (
                <div className="mono mb-1.5 px-2 text-[10px] uppercase tracking-[0.2em] text-faint">
                  {section.title}
                </div>
              )}
              <ul className="grid gap-0.5">
                {section.items.map((item) => {
                  const active = item.to === '/' ? path === '/' : path.startsWith(item.to);
                  return (
                    <li key={item.to}>
                      <Link
                        to={item.to}
                        aria-current={active ? 'page' : undefined}
                        className={cx(
                          'group flex items-center gap-3 rounded-lg px-2.5 py-2 text-sm transition',
                          active
                            ? 'bg-copper/12 text-copper-glow shadow-[inset_2px_0_0_var(--color-copper)]'
                            : 'text-slate/75 hover:bg-zinc hover:text-slate',
                        )}
                      >
                        <span
                          className={
                            active ? 'text-copper' : 'text-faint group-hover:text-copper/80'
                          }
                        >
                          {item.icon}
                        </span>
                        <span className="flex-1">{item.label}</span>
                        {item.to === '/inbox' && inbox > 0 && (
                          <span className="mono rounded-full bg-amber/15 px-1.5 text-[10px] text-amber">
                            {inbox}
                          </span>
                        )}
                        {item.deva && (
                          <span className="deva text-[10px] text-copper/0 transition group-hover:text-copper/40">
                            {item.deva}
                          </span>
                        )}
                      </Link>
                    </li>
                  );
                })}
              </ul>
            </div>
          ))}
        </nav>
      </aside>
      {menu && (
        <div
          className="fixed inset-0 z-30 bg-obsidian/60 md:hidden"
          onClick={() => setMenu(false)}
        />
      )}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-20 flex h-14 items-center gap-3 border-b border-line bg-obsidian/75 px-4 backdrop-blur-md md:px-8">
          <button
            type="button"
            className="rounded-md p-1.5 text-muted md:hidden"
            aria-label="Open navigation"
            onClick={() => setMenu(true)}
          >
            <Boxes size={18} />
          </button>
          <Link
            to="/"
            className="flex items-center gap-2 rounded-full border border-line bg-zinc px-3 py-1 text-xs"
            title="System health"
          >
            <StateDot
              state={
                working
                  ? 'running'
                  : issues.some((c: any) => c.state === 'error')
                    ? 'error'
                    : issues.length
                      ? 'warning'
                      : health.data
                        ? 'ok'
                        : 'idle'
              }
            />
            <span className="mono text-slate/85">
              {signedOut
                ? 'Signed out'
                : working
                  ? `Working${s?.current?.title ? ` · ${s.current.title.slice(0, 40)}` : ''}`
                  : issues.length
                    ? `${issues.length} to check`
                    : health.data
                      ? 'All systems ready'
                      : 'Checking…'}
            </span>
          </Link>
          {(overview.data?.restart_needed ?? []).length > 0 && (
            <span className="mono rounded-full bg-amber/10 px-2.5 py-1 text-[11px] text-amber">
              Restart PANINI to apply changes
            </span>
          )}
          <div className="flex-1" />
          <button
            type="button"
            onClick={() => setPalette(true)}
            className="flex items-center gap-2 rounded-lg border border-line bg-zinc px-3 py-1.5 text-xs text-muted hover:border-copper/60"
          >
            <Radar size={13} className="text-copper" /> Search actions
            <kbd className="mono rounded bg-zinc-3 px-1.5 text-[10px]">Ctrl K</kbd>
          </button>
          <a
            href="/activity"
            className="hidden rounded-lg px-2 py-1.5 text-xs text-muted hover:text-copper-glow sm:inline"
          >
            Live view ↗
          </a>
        </header>
        {signedOut && (
          <div
            role="alert"
            className="border-b border-amber/30 bg-amber/5 px-8 py-3 text-sm text-amber"
          >
            Your console session has ended. Open the web console again from PANINI (tray menu → Open
            web console) to sign in.
          </div>
        )}
        <main id="main" className="min-w-0 flex-1">
          <Outlet />
        </main>
      </div>
      {palette && <Palette onClose={() => setPalette(false)} />}
    </div>
  );
}
