import { useState } from 'react';
import { admin, ago, api, useResource } from '../api';
import { Badge, Button, Card, inputClass, useAction } from './ui';

type Client = {
  id: string;
  name: string;
  grants: string[];
  revoked: number | null;
  last_used: number | null;
};
type Issued = Client & {
  token_file: string;
  stdio: { command: string; args: string[] };
};
const scopes: [string, string][] = [
  ['desktop', 'Windows windows, screenshots and approved control tasks'],
  ['browser', 'Separate managed browser; effects need approval'],
  ['search', 'Web search'],
  ['chrome', 'Your attached Chrome tab (requires browser)'],
  ['pages', 'Read and search your notebook pages'],
  ['sandbox', 'Separate sandbox workspace, browser and approved commands'],
];

export function McpPanel() {
  const data = useResource<{
    clients: Client[];
    enabled: boolean;
    ready: boolean;
    grants: { id: string; subject: string; tools: string[]; expires_at: number }[];
    personas?: { id: string; name: string }[];
  }>(admin('/mcp/clients'));
  const [name, setName] = useState('');
  const [grants, setGrants] = useState(['desktop', 'browser', 'search']);
  const [issued, setIssued] = useState<Issued | null>(null);
  const [grantTool, setGrantTool] = useState('browser_navigate');
  const { busy, run } = useAction();
  const claude = issued
    ? `claude mcp add-json --scope user panini '${JSON.stringify({ type: 'stdio', ...issued.stdio }).replaceAll("'", "''")}'`
    : '';
  const stdio = issued
    ? `[mcp_servers.panini]\ncommand = ${JSON.stringify(issued.stdio.command)}\nargs = ${JSON.stringify(issued.stdio.args)}`
    : '';
  const snippets: [string, string][] = [
    ['Claude Code (PowerShell)', claude],
    ['Codex stdio', stdio],
  ];
  return (
    <Card title="Local MCP clients">
      <p className="mb-3 text-sm text-muted">
        Let coding agents use PANINI’s hands. Each client has its own token and browser context.
        Effects wait for your approval. Owner memories, host shell commands, connectors and
        messaging stay private.
      </p>
      <Badge tone={data.data?.ready ? 'ok' : 'warn'}>
        {data.data?.ready
          ? 'Ready'
          : data.data?.enabled
            ? 'Enable ledger and interrupts in Settings'
            : 'Enable PANINI_MCP_SERVER in Settings'}
      </Badge>
      <form
        className="mt-4 grid gap-3"
        onSubmit={(event) => {
          event.preventDefault();
          void run('create-mcp', async () => {
            const { token: _token, ...setup } = await api<Issued & { token: string }>(
              admin('/mcp/clients'),
              { body: { name, grants } },
            );
            setIssued(setup);
            setName('');
            await data.reload();
          });
        }}
      >
        <input
          className={inputClass}
          value={name}
          onChange={(e) => setName(e.target.value)}
          required
          maxLength={80}
          placeholder="Client name, for example Codex"
          aria-label="MCP client name"
        />
        <fieldset className="grid gap-2 text-sm">
          <legend className="mb-2 text-muted">Client access</legend>
          {scopes.map(([scope, label]) => (
            <label key={scope} className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={grants.includes(scope)}
                onChange={(e) => {
                  let next = e.target.checked
                    ? [...grants, scope]
                    : grants.filter((g) => g !== scope);
                  if (scope === 'chrome' && e.target.checked && !next.includes('browser'))
                    next.push('browser');
                  if (scope === 'browser' && !e.target.checked)
                    next = next.filter((g) => g !== 'chrome');
                  setGrants(next);
                }}
              />
              {label}
            </label>
          ))}
          {(data.data?.personas ?? []).map((persona) => (
            <label key={persona.id} className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={grants.includes(`persona:${persona.id}`)}
                onChange={(e) =>
                  setGrants(
                    e.target.checked
                      ? [...grants, `persona:${persona.id}`]
                      : grants.filter((g) => g !== `persona:${persona.id}`),
                  )
                }
              />
              Share {persona.name} for tasks (includes its instructions)
            </label>
          ))}
        </fieldset>
        <Button type="submit" tone="primary" busy={busy === 'create-mcp'}>
          Register client
        </Button>
      </form>
      {issued && (
        <section
          className="mt-4 grid gap-3 rounded-lg border border-amber/30 bg-amber/5 p-3"
          aria-label="One-time MCP setup"
        >
          <p className="text-sm text-amber">
            Setup is shown once. Both clients use a token file protected for your Windows account.
          </p>
          {snippets.map(([title, value]) => (
            <div key={title}>
              <div className="flex items-center justify-between text-sm">
                <strong>{title}</strong>
                <Button
                  tone="ghost"
                  onClick={() =>
                    run(
                      'copy-' + title,
                      () => navigator.clipboard.writeText(value),
                      'Setup copied.',
                    )
                  }
                >
                  Copy
                </Button>
              </div>
              <pre className="mono mt-2 max-w-full whitespace-pre-wrap break-all text-xs">
                {value}
              </pre>
            </div>
          ))}
          <p className="text-xs text-muted">
            The stdio setup uses this installation’s executable and protected token file.
          </p>
          <Button tone="ghost" onClick={() => setIssued(null)}>
            Hide setup
          </Button>
        </section>
      )}
      <ul className="mt-4 grid gap-2">
        {(data.data?.clients ?? []).map((client) => (
          <li key={client.id} className="rounded-lg border border-line p-3">
            <div className="flex items-center justify-between gap-3">
              <strong className="text-sm">{client.name}</strong>
              {client.revoked !== null ? (
                <Badge>Revoked</Badge>
              ) : (
                <Button
                  tone="ghost"
                  busy={busy === client.id}
                  onClick={() =>
                    run(
                      client.id,
                      async () => {
                        await api(admin(`/mcp/clients/${client.id}`), { method: 'DELETE' });
                        if (issued?.id === client.id) setIssued(null);
                        await data.reload();
                      },
                      'Client revoked. Active calls stopped.',
                    )
                  }
                >
                  Revoke
                </Button>
              )}
            </div>
            <p className="mt-1 text-xs text-muted">
              {client.grants.join(', ') || 'Status and own receipts only'} ·{' '}
              {client.last_used ? `used ${ago(client.last_used)}` : 'never used'}
            </p>
            {client.revoked === null && client.grants.includes('browser') && (
              <div className="mt-3 grid gap-2">
                <label className="text-xs text-muted">
                  Allow one tool’s navigation or input for one hour. Submissions and stronger
                  effects still ask.
                  <select
                    className={`${inputClass} mt-1`}
                    value={grantTool}
                    onChange={(event) => setGrantTool(event.target.value)}
                  >
                    {[
                      'browser_navigate',
                      'browser_navigate_back',
                      'browser_type',
                      'browser_select_option',
                      'browser_press_key',
                    ].map((tool) => (
                      <option key={tool}>{tool}</option>
                    ))}
                  </select>
                </label>
                <Button
                  tone="ghost"
                  busy={busy === 'grant-' + client.id}
                  onClick={() =>
                    run(
                      'grant-' + client.id,
                      async () => {
                        await api(admin(`/mcp/clients/${client.id}/grants`), {
                          body: { tool: grantTool, seconds: 3600 },
                        });
                        await data.reload();
                      },
                      'Temporary permission created.',
                    )
                  }
                >
                  Allow for one hour
                </Button>
              </div>
            )}
            {(data.data?.grants ?? [])
              .filter((grant) => grant.subject === `mcp_client:${client.id}`)
              .map((grant) => (
                <div
                  className="mt-2 flex items-center justify-between gap-2 text-xs text-muted"
                  key={grant.id}
                >
                  <span>
                    {grant.tools.join(', ')} ·{' '}
                    {grant.expires_at > Date.now() / 1000 ? 'temporary permission' : 'expired'}
                  </span>
                  <Button
                    tone="ghost"
                    onClick={() =>
                      run('remove-' + grant.id, async () => {
                        await api(admin(`/mcp/clients/${client.id}/grants/${grant.id}`), {
                          method: 'DELETE',
                        });
                        await data.reload();
                      })
                    }
                  >
                    Remove
                  </Button>
                </div>
              ))}
          </li>
        ))}
      </ul>
    </Card>
  );
}
