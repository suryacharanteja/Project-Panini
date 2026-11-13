import { Copy, KeyRound, LogIn, LogOut, Play, Plus, RefreshCw, Trash2, X } from 'lucide-react';
import { useState } from 'react';
import { admin, ago, api, harness, useResource } from '../api';
import { TeachPanel } from '../components/TeachPanel';
import { McpPanel } from '../components/McpPanel';
import {
  Badge,
  Button,
  Card,
  cx,
  Empty,
  Field,
  inputClass,
  Page,
  StateDot,
  useAction,
} from '../components/ui';

/* ── Skills ─────────────────────────────────────────────────────────────── */

type Skill = {
  name: string;
  description: string;
  origin: string;
  instructions?: string;
  replay?: boolean;
};

const SKILL_TEMPLATE = `1. Restate the goal and what a good result contains.
2. …
3. Finish with a short summary and the evidence.`;

export function SkillsPage() {
  const skills = useResource<{ skills: Skill[] }>(harness('/skills'));
  const [editing, setEditing] = useState<{
    name: string;
    description: string;
    instructions: string;
    isNew: boolean;
  } | null>(null);
  const { busy, run } = useAction();

  const open = async (skill: Skill, copy = false) => {
    const full = await api<Skill>(harness(`/skills/${skill.name}`));
    setEditing({
      name: copy ? `${skill.name}-mine` : skill.name,
      description: full.description,
      instructions: full.instructions ?? '',
      isNew: copy,
    });
  };

  return (
    <Page
      title="Skills"
      deva="कौशल"
      eyebrow="Procedures PANINI follows"
      actions={
        <Button
          tone="primary"
          onClick={() =>
            setEditing({ name: '', description: '', instructions: SKILL_TEMPLATE, isNew: true })
          }
        >
          <Plus size={14} /> New skill
        </Button>
      }
    >
      <TeachPanel />
      <div className={cx('grid gap-5', editing && 'lg:grid-cols-[1fr_1.3fr]')}>
        <Card title="Library">
          {(skills.data?.skills ?? []).length === 0 ? (
            <Empty>No skills yet.</Empty>
          ) : (
            <ul className="derive grid gap-2">
              {skills.data!.skills.map((s) => (
                <li
                  key={s.name}
                  className="rounded-lg border border-line bg-obsidian/50 px-3.5 py-3"
                >
                  <div className="flex items-center gap-2">
                    <span className="mono text-sm text-copper-glow">{s.name}</span>
                    <Badge tone={s.origin === 'user' ? 'copper' : 'neutral'}>{s.origin}</Badge>
                    <div className="flex-1" />
                    {s.replay ? (
                      <Badge tone="copper">Reviewed replay</Badge>
                    ) : s.origin === 'user' ? (
                      <Button tone="ghost" onClick={() => void open(s)}>
                        Edit
                      </Button>
                    ) : (
                      <Button
                        tone="ghost"
                        onClick={() => void open(s, true)}
                        title="Built-in skills are read-only"
                      >
                        <Copy size={13} /> Duplicate
                      </Button>
                    )}
                  </div>
                  <p className="mt-1 text-xs text-muted">{s.description}</p>
                </li>
              ))}
            </ul>
          )}
          <p className="mt-4 text-xs text-faint">
            PANINI sees only each skill&apos;s name and description, and loads the full steps when a
            task fits.
          </p>
        </Card>
        {editing && (
          <Card
            title={editing.isNew ? 'New skill' : `Edit ${editing.name}`}
            deva="रचना"
            actions={
              <Button tone="ghost" onClick={() => setEditing(null)} aria-label="Close editor">
                <X size={14} />
              </Button>
            }
          >
            <form
              className="grid gap-4"
              onSubmit={(e) => {
                e.preventDefault();
                void run(
                  'save',
                  async () => {
                    await api(admin(`/skills/${editing.name}`), {
                      method: 'PUT',
                      body: {
                        description: editing.description,
                        instructions: editing.instructions,
                      },
                    });
                    setEditing(null);
                    await skills.reload();
                  },
                  'Skill saved. PANINI can use it now.',
                );
              }}
            >
              <Field label="Name" hint="Lowercase letters, digits and hyphens, e.g. weekly-report.">
                <input
                  className={inputClass + ' mono'}
                  required
                  pattern="[a-z0-9][a-z0-9-]{1,62}"
                  disabled={!editing.isNew}
                  value={editing.name}
                  onChange={(e) => setEditing({ ...editing, name: e.target.value })}
                />
              </Field>
              <Field
                label="When to use it"
                hint="This is what PANINI reads to decide whether the skill fits a task."
              >
                <input
                  className={inputClass}
                  required
                  maxLength={300}
                  value={editing.description}
                  onChange={(e) => setEditing({ ...editing, description: e.target.value })}
                  placeholder="Build my weekly status report from GitHub and my calendar."
                />
              </Field>
              <Field label="Steps (Markdown)">
                <textarea
                  className={inputClass + ' mono min-h-64 text-xs leading-relaxed'}
                  required
                  value={editing.instructions}
                  onChange={(e) => setEditing({ ...editing, instructions: e.target.value })}
                />
              </Field>
              <div className="flex flex-wrap gap-2">
                <Button tone="primary" type="submit" busy={busy === 'save'}>
                  Save skill
                </Button>
                {!editing.isNew && (
                  <Button
                    tone="danger"
                    busy={busy === 'delete'}
                    onClick={() =>
                      confirm(`Delete the skill ${editing.name}?`) &&
                      void run(
                        'delete',
                        async () => {
                          await api(admin(`/skills/${editing.name}`), { method: 'DELETE' });
                          setEditing(null);
                          await skills.reload();
                        },
                        'Skill deleted.',
                      )
                    }
                  >
                    <Trash2 size={14} /> Delete
                  </Button>
                )}
              </div>
            </form>
          </Card>
        )}
      </div>
    </Page>
  );
}

/* ── Connectors ─────────────────────────────────────────────────────────── */

type Draft = {
  name: string;
  kind: 'remote' | 'local';
  url: string;
  command: string;
  args: string;
  env: { key: string; value: string }[];
  description: string;
};

const EMPTY_DRAFT: Draft = {
  name: '',
  kind: 'remote',
  url: '',
  command: '',
  args: '',
  env: [],
  description: '',
};

/** Starting points only: each fills the form, and nothing is installed until you save. */
const DIRECTORY: (Partial<Draft> & { title: string; blurb: string })[] = [
  {
    title: 'GitHub',
    blurb: 'Issues, pull requests and code (remote, sign-in).',
    name: 'github',
    kind: 'remote',
    url: 'https://api.githubcopilot.com/mcp/',
  },
  {
    title: 'Linear',
    blurb: 'Issues and projects (remote, sign-in).',
    name: 'linear',
    kind: 'remote',
    url: 'https://mcp.linear.app/mcp',
  },
  {
    title: 'Notion',
    blurb: 'Pages and databases (remote, sign-in).',
    name: 'notion',
    kind: 'remote',
    url: 'https://mcp.notion.com/mcp',
  },
  {
    title: 'Files on this PC',
    blurb: 'Read and search a folder you choose (local).',
    name: 'files',
    kind: 'local',
    command: 'npx',
    args: '-y @modelcontextprotocol/server-filesystem D:/notes',
  },
  {
    title: 'Fetch web pages',
    blurb: 'Fetch a URL as text (local, needs uv).',
    name: 'fetch',
    kind: 'local',
    command: 'uvx',
    args: 'mcp-server-fetch',
  },
  {
    title: 'Test server',
    blurb: 'Every MCP feature, for trying things out (local).',
    name: 'everything',
    kind: 'local',
    command: 'npx',
    args: '-y @modelcontextprotocol/server-everything',
  },
];

export function ConnectorsPage() {
  const connectors = useResource<any>(admin('/connectors'));
  const receipts = useResource<any>(admin('/connectors/receipts'), 10000);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [trying, setTrying] = useState<{
    server: string;
    tool: string;
    args: string;
    result?: string;
  } | null>(null);
  const { busy, run } = useAction();

  const save = () =>
    run(
      'save',
      async () => {
        const d = draft!;
        await api(admin(`/connectors/${d.name}`), {
          method: 'PUT',
          body:
            d.kind === 'remote'
              ? { kind: 'remote', url: d.url.trim(), description: d.description }
              : {
                  kind: 'local',
                  command: d.command.trim(),
                  args:
                    d.args.match(/(?:[^\s"]+|"[^"]*")+/g)?.map((a) => a.replace(/^"|"$/g, '')) ??
                    [],
                  env: Object.fromEntries(
                    d.env.filter((e) => e.key.trim()).map((e) => [e.key.trim(), e.value]),
                  ),
                  description: d.description,
                },
        });
        setDraft(null);
        await connectors.reload();
      },
      'Connector saved and started.',
    );

  const rows: any[] = connectors.data?.connectors ?? [];

  return (
    <Page
      title="Connectors"
      deva="सेतु"
      eyebrow="Tools from your other apps (MCP)"
      actions={
        <>
          <Button
            busy={busy === 'reload'}
            onClick={() => run('reload', () => connectors.reload(), 'Reloaded.')}
          >
            <RefreshCw size={14} /> Refresh
          </Button>
          <Button tone="primary" onClick={() => setDraft({ ...EMPTY_DRAFT })}>
            <Plus size={14} /> Add connector
          </Button>
        </>
      }
    >
      <div className="grid gap-5">
        {draft && (
          <Card
            title="Add or change a connector"
            deva="रचना"
            actions={
              <Button tone="ghost" onClick={() => setDraft(null)} aria-label="Close">
                <X size={14} />
              </Button>
            }
          >
            <form
              className="grid gap-4 md:grid-cols-2"
              onSubmit={(e) => {
                e.preventDefault();
                void save();
              }}
            >
              <Field label="Name" hint="Lowercase letters, digits and hyphens.">
                <input
                  className={inputClass + ' mono'}
                  required
                  pattern="[a-z0-9][a-z0-9-]{1,40}"
                  value={draft.name}
                  onChange={(e) => setDraft({ ...draft, name: e.target.value })}
                />
              </Field>
              <Field label="Type">
                <div className="flex gap-2">
                  {(['remote', 'local'] as const).map((k) => (
                    <Button
                      key={k}
                      tone={draft.kind === k ? 'primary' : 'quiet'}
                      onClick={() => setDraft({ ...draft, kind: k })}
                    >
                      {k === 'remote' ? 'Remote (URL)' : 'Local (command)'}
                    </Button>
                  ))}
                </div>
              </Field>
              {draft.kind === 'remote' ? (
                <Field
                  label="Server URL"
                  hint="https only. If it needs you to sign in, a Sign in button appears after saving."
                >
                  <input
                    className={inputClass + ' mono'}
                    required
                    value={draft.url}
                    onChange={(e) => setDraft({ ...draft, url: e.target.value })}
                    placeholder="https://example.com/mcp"
                  />
                </Field>
              ) : (
                <>
                  <Field label="Command">
                    <input
                      className={inputClass + ' mono'}
                      required
                      value={draft.command}
                      onChange={(e) => setDraft({ ...draft, command: e.target.value })}
                      placeholder="npx"
                    />
                  </Field>
                  <Field
                    label="Arguments"
                    hint="Separated by spaces; use quotes for values with spaces."
                  >
                    <input
                      className={inputClass + ' mono'}
                      value={draft.args}
                      onChange={(e) => setDraft({ ...draft, args: e.target.value })}
                    />
                  </Field>
                  <div className="md:col-span-2">
                    <div className="mb-1.5 flex items-center gap-2 text-sm">
                      <KeyRound size={14} className="text-copper" /> Secret settings
                      <span className="text-xs text-faint">
                        (stored encrypted for your Windows account, never in files)
                      </span>
                    </div>
                    <div className="grid gap-2">
                      {draft.env.map((row, i) => (
                        <div key={i} className="flex gap-2">
                          <input
                            className={inputClass + ' mono max-w-56'}
                            placeholder="NAME"
                            value={row.key}
                            onChange={(e) =>
                              setDraft({
                                ...draft,
                                env: draft.env.map((r, j) =>
                                  j === i ? { ...r, key: e.target.value } : r,
                                ),
                              })
                            }
                          />
                          <input
                            className={inputClass}
                            type="password"
                            placeholder="value"
                            value={row.value}
                            onChange={(e) =>
                              setDraft({
                                ...draft,
                                env: draft.env.map((r, j) =>
                                  j === i ? { ...r, value: e.target.value } : r,
                                ),
                              })
                            }
                          />
                          <Button
                            tone="ghost"
                            aria-label="Remove"
                            onClick={() =>
                              setDraft({ ...draft, env: draft.env.filter((_, j) => j !== i) })
                            }
                          >
                            <X size={14} />
                          </Button>
                        </div>
                      ))}
                      <div>
                        <Button
                          onClick={() =>
                            setDraft({ ...draft, env: [...draft.env, { key: '', value: '' }] })
                          }
                        >
                          <Plus size={13} /> Add a secret
                        </Button>
                      </div>
                    </div>
                  </div>
                </>
              )}
              <Field label="Description (optional)">
                <input
                  className={inputClass}
                  value={draft.description}
                  onChange={(e) => setDraft({ ...draft, description: e.target.value })}
                />
              </Field>
              <div className="flex items-end">
                <Button tone="primary" type="submit" busy={busy === 'save'}>
                  Save and connect
                </Button>
              </div>
            </form>
          </Card>
        )}

        <Card title="Your connectors" deva="सक्रिय">
          {connectors.error && <p className="text-sm text-danger">{connectors.error}</p>}
          {rows.length === 0 ? (
            <Empty>No connectors yet. Pick one from the directory below, or add your own.</Empty>
          ) : (
            <ul className="derive grid gap-3">
              {rows.map((c) => (
                <li key={c.name} className="rounded-xl border border-line bg-obsidian/50 p-4">
                  <div className="flex flex-wrap items-center gap-2">
                    <StateDot
                      state={
                        c.state === 'ready' ? 'ok' : c.state === 'sign_in' ? 'warning' : 'error'
                      }
                    />
                    <span className="mono text-sm text-copper-glow">{c.name}</span>
                    <Badge tone="neutral">{c.spec?.kind}</Badge>
                    {c.state === 'sign_in' && <Badge tone="warn">sign-in needed</Badge>}
                    {c.signed_in && <Badge tone="ok">signed in</Badge>}
                    <div className="flex-1" />
                    {c.spec?.kind === 'remote' &&
                      (c.signed_in ? (
                        <Button
                          tone="ghost"
                          onClick={() =>
                            run(
                              'out' + c.name,
                              async () => {
                                await api(harness(`/connectors/${c.name}/sign-out`), {
                                  method: 'POST',
                                });
                                await connectors.reload();
                              },
                              'Signed out.',
                            )
                          }
                        >
                          <LogOut size={13} /> Sign out
                        </Button>
                      ) : (
                        <Button
                          tone="primary"
                          busy={busy === 'in' + c.name}
                          onClick={() =>
                            run(
                              'in' + c.name,
                              async () => {
                                const r = await api<any>(harness(`/connectors/${c.name}/sign-in`), {
                                  method: 'POST',
                                });
                                if (r.url) window.open(r.url, '_blank', 'noopener');
                                else await connectors.reload();
                              },
                              'Finish signing in on the page that opened, then press Refresh.',
                            )
                          }
                        >
                          <LogIn size={13} /> Sign in
                        </Button>
                      ))}
                    <Button
                      tone="ghost"
                      onClick={() =>
                        setDraft({
                          ...EMPTY_DRAFT,
                          name: c.name,
                          kind: c.spec.kind,
                          url: c.spec.url ?? '',
                          command: c.spec.command ?? '',
                          args: (c.spec.args ?? []).join(' '),
                          env: (c.spec.secret_keys ?? []).map((key: string) => ({
                            key,
                            value: '',
                          })),
                          description: c.spec.description ?? '',
                        })
                      }
                    >
                      Edit
                    </Button>
                    <Button
                      tone="ghost"
                      onClick={() =>
                        confirm(
                          `Remove the connector ${c.name}? Its stored sign-in and secrets are deleted too.`,
                        ) &&
                        void run(
                          'rm' + c.name,
                          async () => {
                            await api(admin(`/connectors/${c.name}`), { method: 'DELETE' });
                            await connectors.reload();
                          },
                          'Connector removed.',
                        )
                      }
                    >
                      <Trash2 size={13} />
                    </Button>
                  </div>
                  {c.error && c.state !== 'ready' && (
                    <p className="mt-2 text-xs text-amber">{c.error}</p>
                  )}
                  <p className="mono mt-1 truncate text-[11px] text-faint">
                    {c.spec?.url ?? [c.spec?.command, ...(c.spec?.args ?? [])].join(' ')}
                  </p>
                  {(c.tools ?? []).length > 0 && (
                    <div className="mt-3 flex flex-wrap gap-1.5">
                      {c.tools.map((t: any) => (
                        <div key={t.name} className="flex items-center gap-1">
                          <button
                            type="button"
                            title={t.description}
                            onClick={() =>
                              t.read_only && setTrying({ server: c.name, tool: t.name, args: '{}' })
                            }
                            className={cx(
                              'mono rounded-md border px-2 py-1 text-[11px]',
                              t.read_only
                                ? 'border-gold/30 text-gold hover:bg-gold/10'
                                : 'cursor-default border-line text-muted',
                            )}
                          >
                            {t.name}
                            {t.read_only ? ' · try' : ' · asks first'}
                          </button>
                          {t.read_only && (
                            <Button
                              tone="ghost"
                              className="mono text-[11px]"
                              busy={busy === `grant-${c.name}-${t.name}`}
                              onClick={() =>
                                run(
                                  `grant-${c.name}-${t.name}`,
                                  async () => {
                                    await api(admin(`/connectors/${c.name}/background-grant`), {
                                      body: {
                                        tool: t.name,
                                        allowed: !(c.spec?.background_read_only ?? []).includes(
                                          t.name,
                                        ),
                                      },
                                    });
                                    await connectors.reload();
                                  },
                                  'Background access updated.',
                                )
                              }
                            >
                              {(c.spec?.background_read_only ?? []).includes(t.name)
                                ? 'Background on'
                                : 'Allow background'}
                            </Button>
                          )}
                        </div>
                      ))}
                    </div>
                  )}
                  {trying?.server === c.name && (
                    <div className="mt-3 grid gap-2 rounded-lg border border-gold/25 bg-zinc-2 p-3">
                      <div className="mono text-xs text-gold">Try {trying!.tool} (read-only)</div>
                      <textarea
                        className={inputClass + ' mono text-xs'}
                        rows={3}
                        value={trying!.args}
                        onChange={(e) => setTrying((t) => t && { ...t, args: e.target.value })}
                        aria-label="Arguments as JSON"
                      />
                      <div className="flex gap-2">
                        <Button
                          tone="primary"
                          busy={busy === 'try'}
                          onClick={() =>
                            run('try', async () => {
                              const r = await api<any>(
                                admin(`/connectors/${c.name}/tools/${trying!.tool}/try`),
                                { body: { arguments: JSON.parse(trying!.args || '{}') } },
                              );
                              setTrying((t) => t && { ...t, result: r.text });
                            })
                          }
                        >
                          <Play size={13} /> Run
                        </Button>
                        <Button tone="ghost" onClick={() => setTrying(null)}>
                          Close
                        </Button>
                      </div>
                      {trying!.result !== undefined && (
                        <pre className="mono max-h-60 overflow-auto whitespace-pre-wrap rounded bg-obsidian p-2 text-[11px] text-slate/85">
                          {trying!.result}
                        </pre>
                      )}
                    </div>
                  )}
                </li>
              ))}
            </ul>
          )}
          <p className="mt-4 text-xs text-faint">
            Read-only tools run without asking in the foreground. Allow specific read-only tools for
            background research here. Anything that changes data asks you first in the avatar.
          </p>
        </Card>

        <Card title="Recent connector calls">
          {(receipts.data?.receipts ?? []).length === 0 ? (
            <Empty>No connector calls yet.</Empty>
          ) : (
            <ul className="grid gap-2">
              {receipts.data.receipts.map((receipt: any) => (
                <li
                  key={receipt.id}
                  className="mono flex flex-wrap items-center gap-2 rounded-lg border border-line p-2 text-xs"
                >
                  <Badge tone={receipt.ok ? 'ok' : 'error'}>{receipt.ok ? 'ok' : 'error'}</Badge>
                  <span>
                    {receipt.server}.{receipt.tool}
                  </span>
                  <span className="text-faint">
                    {receipt.read_only ? 'read-only' : 'approved write'} · {ago(receipt.created)}
                  </span>
                </li>
              ))}
            </ul>
          )}
          <p className="mt-3 text-xs text-faint">
            Receipts store hashes of arguments and results, not their contents.
          </p>
        </Card>

        <Card title="Directory" deva="सूची">
          <div className="derive grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {DIRECTORY.map((d) => (
              <button
                key={d.title}
                type="button"
                onClick={() => setDraft({ ...EMPTY_DRAFT, ...d, env: [] } as Draft)}
                className="rounded-xl border border-line bg-obsidian/50 p-4 text-left transition hover:-translate-y-0.5 hover:border-copper/60"
              >
                <div className="flex items-center gap-2">
                  <span className="text-sm">{d.title}</span>
                  <Badge tone="neutral">{d.kind}</Badge>
                </div>
                <p className="mt-1 text-xs text-muted">{d.blurb}</p>
              </button>
            ))}
          </div>
          <p className="mt-3 text-xs text-faint">
            Choosing one only fills the form; nothing is installed until you save.
          </p>
        </Card>
      </div>
    </Page>
  );
}

/* ── Agents (A2A) ───────────────────────────────────────────────────────── */

export function AgentsPage() {
  const agents = useResource<any>(admin('/agents'));
  const [token, setToken] = useState<string | null>(null);
  const [clientName, setClientName] = useState('');
  const [remote, setRemote] = useState({ name: '', url: '', description: '', token: '' });
  const { busy, run } = useAction();
  return (
    <Page title="Agents" deva="कर्ता" eyebrow="Agent-to-agent (A2A)">
      <McpPanel />
      <div className="grid gap-5 lg:grid-cols-2">
        <Card title="Agents that can ask PANINI" deva="आगत">
          <p className="-mt-2 mb-4 text-sm text-muted">
            They send tasks to <code className="mono text-copper-glow">/a2a</code> with a token from
            here. Every request waits for your approval in the Inbox, runs with read-only research,
            and never sees your memories.
          </p>
          <form
            className="flex gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              void run('client', async () => {
                const r = await api<any>(harness('/a2a/clients'), { body: { name: clientName } });
                setToken(r.token);
                setClientName('');
                await agents.reload();
              });
            }}
          >
            <input
              className={inputClass}
              required
              value={clientName}
              onChange={(e) => setClientName(e.target.value)}
              placeholder="Name of the calling agent"
            />
            <Button tone="primary" type="submit" busy={busy === 'client'}>
              Create token
            </Button>
          </form>
          {token && (
            <div className="mono mt-3 break-all rounded-lg border border-amber/30 bg-amber/5 p-3 text-xs text-amber">
              Shown once. Give it to that agent as a Bearer token:
              <br />
              {token}
            </div>
          )}
          <ul className="derive mt-4 grid gap-2">
            {(agents.data?.clients ?? []).map((c: any) => (
              <li
                key={c.id}
                className="flex items-center gap-3 rounded-lg border border-line bg-obsidian/50 px-3 py-2.5"
              >
                <span className="flex-1 text-sm">{c.name}</span>
                <span className="mono text-[11px] text-faint">
                  {c.last_used ? `used ${ago(c.last_used)}` : 'never used'}
                </span>
                <Button
                  tone="ghost"
                  onClick={() =>
                    run(
                      'revoke' + c.id,
                      async () => {
                        await api(harness(`/a2a/clients/${c.id}`), { method: 'DELETE' });
                        await agents.reload();
                      },
                      'Token revoked.',
                    )
                  }
                >
                  Revoke
                </Button>
              </li>
            ))}
          </ul>
        </Card>
        <Card title="Agents PANINI can ask" deva="निर्गत">
          <p className="-mt-2 mb-4 text-sm text-muted">
            PANINI asks you before sending any of them a message. Tokens are stored encrypted.
          </p>
          <form
            className="grid gap-3"
            onSubmit={(e) => {
              e.preventDefault();
              void run(
                'remote',
                async () => {
                  await api(admin(`/agents/${remote.name}`), {
                    method: 'PUT',
                    body: {
                      url: remote.url,
                      description: remote.description,
                      token: remote.token || null,
                    },
                  });
                  setRemote({ name: '', url: '', description: '', token: '' });
                  await agents.reload();
                },
                'Agent saved.',
              );
            }}
          >
            <div className="grid gap-3 sm:grid-cols-2">
              <input
                className={inputClass + ' mono'}
                required
                pattern="[a-z0-9][a-z0-9-]{1,40}"
                placeholder="name"
                value={remote.name}
                onChange={(e) => setRemote({ ...remote, name: e.target.value })}
              />
              <input
                className={inputClass + ' mono'}
                required
                placeholder="https://agent.example/a2a"
                value={remote.url}
                onChange={(e) => setRemote({ ...remote, url: e.target.value })}
              />
              <input
                className={inputClass}
                placeholder="What it is good at"
                value={remote.description}
                onChange={(e) => setRemote({ ...remote, description: e.target.value })}
              />
              <input
                className={inputClass}
                type="password"
                placeholder="Bearer token (optional)"
                value={remote.token}
                onChange={(e) => setRemote({ ...remote, token: e.target.value })}
              />
            </div>
            <div>
              <Button tone="primary" type="submit" busy={busy === 'remote'}>
                Save agent
              </Button>
            </div>
          </form>
          <ul className="derive mt-4 grid gap-2">
            {(agents.data?.agents ?? []).map((a: any) => (
              <li
                key={a.name}
                className="flex items-center gap-3 rounded-lg border border-line bg-obsidian/50 px-3 py-2.5"
              >
                <div className="min-w-0 flex-1">
                  <div className="mono text-sm text-copper-glow">{a.name}</div>
                  <div className="mono truncate text-[11px] text-faint">{a.url}</div>
                </div>
                {a.has_token && <Badge tone="ok">token</Badge>}
                <Button
                  tone="ghost"
                  onClick={() =>
                    run(
                      'rm' + a.name,
                      async () => {
                        await api(admin(`/agents/${a.name}`), { method: 'DELETE' });
                        await agents.reload();
                      },
                      'Agent removed.',
                    )
                  }
                >
                  <Trash2 size={13} />
                </Button>
              </li>
            ))}
            {(agents.data?.agents ?? []).length === 0 && <Empty>None yet.</Empty>}
          </ul>
        </Card>
      </div>
    </Page>
  );
}
