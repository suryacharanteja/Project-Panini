import { Box, Play, Square, TerminalSquare } from 'lucide-react';
import { useEffect, useState } from 'react';
import { api, harness, useResource } from '../api';
import { Badge, Button, Card, Empty, inputClass, Page, Switch, useAction } from '../components/ui';

type Permissions = { enabled: boolean; browser: boolean; files: boolean; shell: boolean };
type Computer = {
  computer: string;
  configured: boolean;
  state: 'not_configured' | 'stopped' | 'running' | 'unavailable';
  permissions: Permissions;
  control?: { holder: string; request?: { id: string; status: string } };
  engine?: string;
  browserOpen?: boolean;
  error?: string;
};

const STATE_TONE: Record<Computer['state'], 'ok' | 'warn' | 'error' | 'neutral'> = {
  running: 'ok',
  stopped: 'neutral',
  not_configured: 'warn',
  unavailable: 'error',
};
const SWITCHES: { key: keyof Permissions; label: string; help: string }[] = [
  { key: 'enabled', label: 'Computer on', help: 'PANINI may use this computer at all.' },
  { key: 'browser', label: 'Browser', help: 'Open and act on web pages inside the sandbox.' },
  { key: 'files', label: 'Files', help: 'Read and write files in its workspace folder.' },
  { key: 'shell', label: 'Shell', help: 'Run commands inside the sandbox (never on this PC).' },
];

/** Live view of a native computer's browser; while the owner holds control, clicks and keys go to the page. */
function LiveView({ id, holding }: { id: string; holding: boolean }) {
  const [tick, setTick] = useState(0);
  const [text, setText] = useState('');
  const [failed, setFailed] = useState(false);
  const path = (p: string) => harness(`/computers/${encodeURIComponent(id)}${p}`);
  useEffect(() => {
    const timer = window.setInterval(() => setTick((t) => t + 1), holding ? 700 : 2000);
    return () => window.clearInterval(timer);
  }, [holding]);
  const send = (action: string, body: unknown) =>
    api(path(`/actions/${action}`), { body }).then(() => setTick((t) => t + 1));
  return (
    <div className="grid gap-2">
      {failed ? (
        <p className="text-xs text-faint">The browser shows here once PANINI opens a page.</p>
      ) : null}
      <img
        src={`${path('/frame')}?t=${tick}`}
        alt={`Live view of ${id}'s browser`}
        className={`w-full rounded-md border border-line ${holding ? 'cursor-crosshair' : ''}`}
        onLoad={() => setFailed(false)}
        onError={() => setFailed(true)}
        hidden={failed}
        onClick={(event) => {
          if (!holding) return;
          const img = event.currentTarget;
          const x = Math.round((event.nativeEvent.offsetX / img.clientWidth) * img.naturalWidth);
          const y = Math.round((event.nativeEvent.offsetY / img.clientHeight) * img.naturalHeight);
          void send('human_click', { x, y });
        }}
        onWheel={(event) =>
          holding && void send('human_scroll', { deltaY: Math.round(event.deltaY) })
        }
      />
      {holding && (
        <form
          className="flex gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            void send('human_type', { text }).then(() => setText(''));
          }}
        >
          <input
            className={inputClass}
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="Type into the page"
            aria-label="Type into the sandbox page"
          />
          <Button type="submit" disabled={!text}>
            Type
          </Button>
          {['Enter', 'Tab', 'Backspace', 'Escape'].map((key) => (
            <Button key={key} onClick={() => void send('human_key', { key })}>
              {key}
            </Button>
          ))}
        </form>
      )}
    </div>
  );
}

function ComputerCard({ computer, reload }: { computer: Computer; reload: () => void }) {
  const { busy, run } = useAction();
  const [command, setCommand] = useState('');
  const [output, setOutput] = useState('');
  const id = computer.computer;
  const path = (p: string) => harness(`/computers/${encodeURIComponent(id)}${p}`);
  const act = (key: string, p: string, body: unknown = {}, method = 'POST') =>
    run(key, async () => {
      const result = await api(path(p), { method, body });
      reload();
      return result;
    });

  return (
    <Card
      title={
        <span className="flex items-center gap-2">
          <Box size={16} /> {id}{' '}
          <Badge tone={STATE_TONE[computer.state]}>{computer.state.replace('_', ' ')}</Badge>
          {computer.control?.holder === 'human' && <Badge tone="copper">you have control</Badge>}
        </span>
      }
      actions={
        <span className="flex gap-2">
          {computer.state === 'running' ? (
            <Button busy={busy === 'stop'} onClick={() => act('stop', '/stop')}>
              <Square size={14} /> Stop
            </Button>
          ) : (
            <Button tone="primary" busy={busy === 'start'} onClick={() => act('start', '/start')}>
              <Play size={14} /> Start
            </Button>
          )}
        </span>
      }
    >
      {computer.error && <p className="mb-3 text-sm text-danger">{computer.error}</p>}
      <div className="grid gap-3 sm:grid-cols-2">
        {SWITCHES.map((s) => (
          <label key={s.key} className="flex items-start gap-3 text-sm">
            <Switch
              checked={computer.permissions[s.key]}
              label={s.label}
              onChange={(value) =>
                act(`perm-${s.key}`, '/permissions', { [s.key]: value }, 'PATCH')
              }
            />
            <span>
              <span className="text-slate">{s.label}</span>
              <span className="block text-xs text-faint">{s.help}</span>
            </span>
          </label>
        ))}
      </div>
      {computer.state === 'running' && (
        <div className="mt-4 grid gap-2">
          <div className="flex gap-2">
            <Button onClick={() => act('take', '/control/take')}>
              Take control of its browser
            </Button>
            <Button onClick={() => act('release', '/control/release')}>Hand back</Button>
          </div>
          {computer.engine === 'native' && computer.permissions.browser && (
            <LiveView id={id} holding={computer.control?.holder === 'human'} />
          )}
          <form
            className="flex gap-2"
            onSubmit={async (event) => {
              event.preventDefault();
              await run('exec', async () => {
                const result = await api<{
                  result: { stdout?: string; stderr?: string; exitCode?: number };
                }>(path('/actions/exec'), { body: { command, timeoutMs: 30000 } });
                const r = result.result || {};
                setOutput(
                  `${r.stdout ?? ''}${r.stderr ? `\n${r.stderr}` : ''}\n(exit ${r.exitCode ?? '?'})`,
                );
              });
            }}
          >
            <TerminalSquare size={16} className="mt-2 text-faint" />
            <input
              className={inputClass}
              value={command}
              onChange={(e) => setCommand(e.target.value)}
              placeholder="Run a command inside the sandbox"
              aria-label="Sandbox command"
            />
            <Button type="submit" busy={busy === 'exec'} disabled={!command.trim()}>
              Run
            </Button>
          </form>
          {output && (
            <pre className="mono max-h-64 overflow-auto rounded-md bg-zinc-2 p-3 text-xs">
              {output}
            </pre>
          )}
        </div>
      )}
    </Card>
  );
}

/** PANINI's own isolated computers on Docker: start, stop, permissions, take-over, terminal (V2-11). */
type Runtime = {
  engines: string[];
  autostart: string;
  approved: boolean;
  setup: { phase: string; message: string; ok: boolean | null; log: string[] };
};

export function ComputersPage() {
  const list = useResource<{ configured: boolean; runtime?: Runtime; computers: Computer[] }>(
    harness('/computers'),
    3000,
  );
  const { busy, run } = useAction();
  const runtime = list.data?.runtime;
  const setup = runtime?.setup;
  const working = !!setup && !['idle', 'ready', 'failed'].includes(setup.phase);
  const down = list.data?.computers.some((c) => c.state !== 'running' && c.state !== 'stopped');
  return (
    <Page title="Computers" deva="यन्त्र" eyebrow="Sandbox">
      {list.data && (!list.data.configured || down || working || setup?.phase === 'failed') && (
        <Card title="Set up the sandbox">
          <p className="mb-3 text-sm text-muted">
            One click: PANINI creates its sandbox tokens, starts the container engine
            {runtime?.engines.length ? ` (${runtime.engines.join(' or ')})` : ''}, builds the pinned
            computer images the first time, and starts the sandbox supervisor. You never start
            Docker yourself.
          </p>
          {runtime && runtime.engines.length === 0 && (
            <p className="mb-3 text-sm text-danger">
              No container engine is installed. Install Docker Desktop or Podman Desktop, then try
              again.
            </p>
          )}
          {setup && setup.phase !== 'idle' && (
            <p
              className={
                setup.ok === false ? 'mb-3 text-sm text-danger' : 'mb-3 text-sm text-slate'
              }
            >
              {setup.message}
            </p>
          )}
          <Button
            tone="primary"
            busy={busy === 'setup' || working}
            disabled={runtime?.engines.length === 0}
            onClick={() =>
              run('setup', async () => {
                await api(harness('/computers/setup'), { body: {} });
                list.reload();
              })
            }
          >
            {working ? 'Setting up…' : 'Set up the sandbox'}
          </Button>
        </Card>
      )}
      {list.error && <Empty>{list.error}</Empty>}
      <div className="grid gap-4">
        {list.data?.computers.map((c) => (
          <ComputerCard key={c.computer} computer={c} reload={list.reload} />
        ))}
      </div>
    </Page>
  );
}
