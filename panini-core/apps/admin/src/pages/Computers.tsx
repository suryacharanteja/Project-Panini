import { Box, Play, Square, TerminalSquare } from 'lucide-react';
import { useState } from 'react';
import { api, harness, useResource } from '../api';
import { Badge, Button, Card, Empty, inputClass, Page, Switch, useAction } from '../components/ui';

type Permissions = { enabled: boolean; browser: boolean; files: boolean; shell: boolean };
type Computer = {
  computer: string;
  configured: boolean;
  state: 'not_configured' | 'stopped' | 'running' | 'unavailable';
  permissions: Permissions;
  control?: { holder: string; request?: { id: string; status: string } };
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
export function ComputersPage() {
  const list = useResource<{ configured: boolean; computers: Computer[] }>(
    harness('/computers'),
    10000,
  );
  const { busy, run } = useAction();
  return (
    <Page title="Computers" deva="यन्त्र" eyebrow="Sandbox">
      {list.data && !list.data.configured && (
        <Card title="Set up the sandbox">
          <p className="mb-3 text-sm text-muted">
            Creates the sandbox tokens in PANINI's secret store. Then start Docker Desktop and run{' '}
            <code className="mono">scripts/panini-sandbox.py build</code> and{' '}
            <code className="mono">up</code>.
          </p>
          <Button
            tone="primary"
            busy={busy === 'setup'}
            onClick={() =>
              run('setup', async () => {
                await api(harness('/computers/setup'), { body: {} });
                list.reload();
              })
            }
          >
            Set up the sandbox
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
