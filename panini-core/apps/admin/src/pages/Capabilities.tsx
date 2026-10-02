import { api, harness, useResource } from '../api';
import { Badge, Card, Empty, Page, StateDot, Switch, useAction } from '../components/ui';

type Tool = { name: string; effect: string; group: string; offered: boolean; state: string };
type Capability = { title: string; summary: string; enabled: boolean; tools: Tool[] };
type Registry = {
  flag: boolean;
  errors: string[];
  inventory: Record<string, Capability>;
};

const EFFECT_TONE: Record<string, 'neutral' | 'ok' | 'warn' | 'error' | 'copper'> = {
  observe: 'ok',
  navigate: 'ok',
  input: 'neutral',
  local_write: 'copper',
  local_exec: 'copper',
  submit: 'warn',
  share_out: 'warn',
  communicate: 'warn',
  transact: 'error',
  account: 'error',
  destroy: 'error',
  install: 'error',
  credential: 'error',
};

/** Every capability PANINI has, from its manifest: tools, what they do to the world, and readiness. */
export function CapabilitiesPage() {
  const registry = useResource<Registry>(harness('/registry'));
  const { busy, run } = useAction();
  const toggle = (id: string, enabled: boolean) =>
    run(
      id,
      async () => {
        await api(harness(`/registry/${id}`), { method: 'PATCH', body: { enabled } });
        await registry.reload();
      },
      enabled ? 'Capability on' : 'Capability off',
    );

  return (
    <Page title="Capabilities" deva="शक्ति" eyebrow="Registry">
      {registry.data && !registry.data.flag && (
        <p className="mb-4 text-sm text-muted">
          The v2 registry is off (Settings → System → PANINI v2 → registry). This list is what it
          will serve; switches take effect when it is on.
        </p>
      )}
      {registry.data?.errors.map((e) => (
        <p key={e} role="alert" className="mb-2 text-sm text-danger">
          {e}
        </p>
      ))}
      <div className="grid gap-4">
        {!registry.data && <Empty>{registry.error || 'Loading capabilities…'}</Empty>}
        {registry.data &&
          Object.entries(registry.data.inventory).map(([id, cap]) => (
            <Card
              key={id}
              title={cap.title}
              actions={
                id !== 'control' ? (
                  <Switch
                    checked={cap.enabled}
                    label={`${cap.title} enabled`}
                    onChange={(value) => busy !== id && toggle(id, value)}
                  />
                ) : undefined
              }
            >
              <p className="mb-3 text-sm text-muted">{cap.summary}</p>
              <ul className="grid gap-1.5">
                {cap.tools.map((tool) => (
                  <li key={tool.name} className="flex flex-wrap items-center gap-2 text-sm">
                    <StateDot state={tool.state === 'ready' ? 'ok' : 'warning'} />
                    <span className="mono text-slate">{tool.name}</span>
                    <Badge tone={EFFECT_TONE[tool.effect] ?? 'neutral'}>{tool.effect}</Badge>
                    {tool.group !== 'core' && <Badge>{tool.group}</Badge>}
                    {!tool.offered && <Badge>routed</Badge>}
                    {tool.state !== 'ready' && (
                      <span className="text-xs text-faint">{tool.state}</span>
                    )}
                  </li>
                ))}
              </ul>
            </Card>
          ))}
      </div>
    </Page>
  );
}
