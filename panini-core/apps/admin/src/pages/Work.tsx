import { Download, RefreshCw, Trash2 } from 'lucide-react';
import { useEffect, useState } from 'react';
import { ago, api, harness, useResource, when } from '../api';
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

const STATUS_TONE: Record<string, any> = {
  completed: 'ok',
  failed: 'error',
  cancelled: 'neutral',
  needs_review: 'warn',
  running: 'active',
  queued: 'copper',
};

export function InboxPage() {
  const reviews = useResource<any>(harness('/reviews'), 10000);
  const notices = useResource<any>(harness('/notices'), 10000);
  const { busy, run } = useAction();
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const reload = () => Promise.all([reviews.reload(), notices.reload()]);

  const decide = (id: string, decision: 'rerun' | 'dismiss') =>
    run(
      id + decision,
      async () => {
        await api(harness(`/reviews/${id}`), { body: { decision, answer: answers[id] ?? '' } });
        await reload();
      },
      decision === 'rerun' ? 'Approved. It is queued.' : 'Declined.',
    );

  return (
    <Page
      title="Inbox"
      deva="सूचना"
      eyebrow="What needs you"
      actions={
        <Button
          busy={busy === 'suggest'}
          onClick={() =>
            run(
              'suggest',
              async () => {
                const r = await api<any>(harness('/suggestions/refresh'), { method: 'POST' });
                await reload();
                return r;
              },
              'Checked for suggestions.',
            )
          }
        >
          <RefreshCw size={14} /> Check for suggestions
        </Button>
      }
    >
      <div className="grid gap-5">
        <Card title="Waiting for your decision" deva="प्रतीक्षा">
          {(reviews.data?.reviews ?? []).length === 0 ? (
            <Empty>Nothing is waiting for you.</Empty>
          ) : (
            <ul className="derive grid gap-3">
              {reviews.data.reviews.map((r: any) => (
                <li key={r.id} className="rounded-xl border border-amber/25 bg-obsidian/50 p-4">
                  <div className="mb-1 flex items-center gap-2">
                    <Badge tone="warn">
                      {r.kind === 'approval'
                        ? 'another agent asks'
                        : r.kind === 'outcome_unknown'
                          ? 'outcome unknown'
                          : 'question'}
                    </Badge>
                    <span className="mono text-[11px] text-faint">expires {when(r.expires)}</span>
                  </div>
                  <p className="whitespace-pre-wrap text-sm">{r.message}</p>
                  <div className="mt-3 flex flex-wrap gap-2">
                    <input
                      className={inputClass + ' max-w-md'}
                      placeholder={r.kind === 'question' ? 'Your answer' : 'Optional note'}
                      aria-label="Answer"
                      value={answers[r.id] ?? ''}
                      onChange={(e) => setAnswers((a) => ({ ...a, [r.id]: e.target.value }))}
                    />
                    <Button
                      tone="primary"
                      busy={busy === r.id + 'rerun'}
                      onClick={() => decide(r.id, 'rerun')}
                    >
                      {r.kind === 'approval' ? 'Approve' : 'Run again'}
                    </Button>
                    <Button
                      busy={busy === r.id + 'dismiss'}
                      onClick={() => decide(r.id, 'dismiss')}
                    >
                      Decline
                    </Button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Card>
        <Card title="Updates and suggestions" deva="वार्ता">
          {(notices.data?.notices ?? []).length === 0 ? (
            <Empty>
              No updates yet. Results of background work, watch alerts and suggestions appear here.
            </Empty>
          ) : (
            <ul className="derive grid gap-3">
              {notices.data.notices.map((n: any) => (
                <li key={n.id} className="rounded-xl border border-line bg-obsidian/50 p-4">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge
                      tone={n.kind === 'watch' ? 'warn' : n.kind === 'suggestion' ? 'copper' : 'ok'}
                    >
                      {n.kind}
                    </Badge>
                    {n.status === 'unread' && <StateDot state="warning" />}
                    <span className="text-sm font-medium">{n.title}</span>
                    <span className="mono ml-auto text-[11px] text-faint">{ago(n.created)}</span>
                  </div>
                  <p className="mt-2 whitespace-pre-wrap text-sm text-slate/85">{n.body}</p>
                  <div className="mt-3 flex flex-wrap gap-2">
                    {(n.action?.type === 'schedule' || n.action?.type === 'skill') && (
                      <Button
                        tone="primary"
                        busy={busy === n.id + 'accept'}
                        onClick={() =>
                          run(
                            n.id + 'accept',
                            async () => {
                              await api(harness(`/notices/${n.id}/accept`), { method: 'POST' });
                              await reload();
                            },
                            'Done. It is on your automations list.',
                          )
                        }
                      >
                        {n.action.type === 'schedule' ? 'Make it a routine' : 'Draft the skill'}
                      </Button>
                    )}
                    {n.status === 'unread' && (
                      <Button
                        onClick={() =>
                          run(n.id + 'read', async () => {
                            await api(harness(`/notices/${n.id}`), { body: { status: 'read' } });
                            await reload();
                          })
                        }
                      >
                        Mark read
                      </Button>
                    )}
                    <Button
                      tone="ghost"
                      onClick={() =>
                        run(n.id + 'dismiss', async () => {
                          await api(harness(`/notices/${n.id}`), { body: { status: 'dismissed' } });
                          await reload();
                        })
                      }
                    >
                      Dismiss
                    </Button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </Page>
  );
}

export function RunsPage() {
  const tasks = useResource<any>(harness('/tasks'), 8000);
  const [open, setOpen] = useState<string | null>(null);
  const detail = useResource<any>(open ? harness(`/tasks/${open}`) : null, open ? 5000 : 0);
  return (
    <Page
      title="Activity"
      deva="क्रिया"
      eyebrow="Every run, with its derivation"
      actions={
        <a href="/activity" className="text-sm text-copper-light hover:text-copper-glow">
          Open the live view ↗
        </a>
      }
    >
      <div className="grid gap-5 lg:grid-cols-[1fr_1.1fr]">
        <Card title="Background and scheduled runs">
          {(tasks.data?.tasks ?? []).length === 0 ? (
            <Empty>No runs yet.</Empty>
          ) : (
            <ul className="derive grid gap-2">
              {tasks.data.tasks.map((t: any) => (
                <li key={t.id}>
                  <button
                    type="button"
                    onClick={() => setOpen(t.id)}
                    className={
                      'w-full rounded-lg border px-3 py-2.5 text-left transition ' +
                      (open === t.id
                        ? 'border-copper/70 bg-copper/10'
                        : 'border-line bg-obsidian/50 hover:border-copper/40')
                    }
                  >
                    <div className="flex items-center gap-2">
                      <Badge tone={STATUS_TONE[t.status] ?? 'neutral'}>
                        {t.kind === 'watch' ? `watch · ${t.status}` : t.status}
                      </Badge>
                      <span className="truncate text-sm">{t.title}</span>
                    </div>
                    <div className="mono mt-1 text-[11px] text-faint">
                      {t.origin} · {t.every_minutes ? `every ${t.every_minutes} min` : 'once'} ·
                      created {ago(t.created)}
                    </div>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Card>
        <Card title={detail.data ? detail.data.title : 'Run trace'} deva="व्युत्पत्ति">
          {!open ? (
            <Empty>
              Select a run to see how it was derived: each step, the evidence and the outcome.
            </Empty>
          ) : !detail.data ? (
            <p className="text-sm text-faint">Loading…</p>
          ) : (
            <>
              {detail.data.result && (
                <div className="mb-4 rounded-lg border border-gold/30 bg-gold/5 p-3 text-sm">
                  <Badge tone={detail.data.result.outcome === 'verified_complete' ? 'ok' : 'warn'}>
                    {detail.data.result.outcome}
                  </Badge>
                  <p className="mt-2 whitespace-pre-wrap">{detail.data.result.message}</p>
                </div>
              )}
              <ol className="derive relative ml-2 border-l border-copper/30 pl-5">
                {detail.data.events.map((e: any) => (
                  <li key={e.id} className="relative mb-3">
                    <span className="absolute -left-[25px] top-1.5 size-2.5 rounded-full border border-copper bg-obsidian" />
                    <div className="mono text-[11px] text-copper-light">
                      {new Date(e.at * 1000).toLocaleTimeString()} · {e.kind}
                    </div>
                    <div className="mono whitespace-pre-wrap break-words text-xs text-slate/80">
                      {String(e.detail).replace(/^"|"$/g, '')}
                    </div>
                  </li>
                ))}
              </ol>
            </>
          )}
        </Card>
      </div>
    </Page>
  );
}

export function ProjectsPage() {
  const projects = useResource<any>(harness('/projects'), 8000);
  const [selected, setSelected] = useState<string | null>(null);
  const detail = useResource<any>(selected ? harness(`/projects/${selected}`) : null, 8000);
  const plan = useResource<any>(selected ? harness(`/projects/${selected}/plan`) : null, 8000);
  const [title, setTitle] = useState('');
  const [goal, setGoal] = useState('');
  const [context, setContext] = useState('');
  const [editContext, setEditContext] = useState('');
  const [taskGoal, setTaskGoal] = useState('');
  const { busy, run } = useAction();
  const current = detail.data?.id === selected ? detail.data : null;
  const currentPlan = plan.data?.plan?.project_id === selected ? plan.data.plan : null;
  useEffect(() => {
    setEditContext(current?.context ?? '');
  }, [current?.id, current?.updated]);

  return (
    <Page title="Projects" deva="योजना" eyebrow="Work that continues over time">
      <div className="grid gap-5 lg:grid-cols-[1fr_1.2fr]">
        <div className="grid content-start gap-5">
          <Card title="New project">
            <form
              className="grid gap-3"
              onSubmit={(event) => {
                event.preventDefault();
                void run(
                  'create-project',
                  async () => {
                    const created = await api<any>(harness('/projects'), {
                      body: { title, goal, context },
                    });
                    setTitle('');
                    setGoal('');
                    setContext('');
                    await projects.reload();
                    setSelected(created.id);
                  },
                  'Project created.',
                );
              }}
            >
              <Field label="Name">
                <input
                  className={inputClass}
                  required
                  maxLength={120}
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                />
              </Field>
              <Field label="Goal">
                <textarea
                  className={inputClass}
                  required
                  maxLength={4000}
                  rows={3}
                  value={goal}
                  onChange={(e) => setGoal(e.target.value)}
                />
              </Field>
              <Field label="Context for future tasks">
                <textarea
                  className={inputClass}
                  maxLength={12000}
                  rows={3}
                  value={context}
                  onChange={(e) => setContext(e.target.value)}
                />
              </Field>
              <Button type="submit" tone="primary" busy={busy === 'create-project'}>
                Create project
              </Button>
            </form>
          </Card>
          <Card title="Your projects">
            {(projects.data?.projects ?? []).length === 0 ? (
              <Empty>No projects yet.</Empty>
            ) : (
              <ul className="grid gap-2">
                {projects.data.projects.map((project: any) => (
                  <li key={project.id}>
                    <button
                      type="button"
                      onClick={() => setSelected(project.id)}
                      className={
                        'w-full rounded-lg border p-3 text-left ' +
                        (selected === project.id
                          ? 'border-copper bg-copper/10'
                          : 'border-line hover:border-copper/40')
                      }
                    >
                      <div className="flex items-center gap-2">
                        <Badge tone={project.status === 'active' ? 'ok' : 'neutral'}>
                          {project.status}
                        </Badge>
                        <span>{project.title}</span>
                      </div>
                      <p className="mt-1 truncate text-xs text-muted">{project.goal}</p>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </div>
        <Card title={current?.title ?? 'Project details'}>
          {!selected || !current ? (
            <Empty>Select a project to view its tasks.</Empty>
          ) : (
            <div className="grid gap-4">
              <p className="whitespace-pre-wrap text-sm">{current.goal}</p>
              <form
                className="grid gap-2"
                onSubmit={(event) => {
                  event.preventDefault();
                  void run(
                    'save-context',
                    async () => {
                      await api(harness(`/projects/${selected}`), {
                        method: 'PATCH',
                        body: { context: editContext },
                      });
                      await Promise.all([detail.reload(), projects.reload()]);
                    },
                    'Project context saved.',
                  );
                }}
              >
                <Field label="Context for future tasks">
                  <textarea
                    className={inputClass}
                    rows={4}
                    maxLength={12000}
                    value={editContext}
                    onChange={(event) => setEditContext(event.target.value)}
                  />
                </Field>
                <Button
                  type="submit"
                  busy={busy === 'save-context'}
                  disabled={editContext === current.context}
                >
                  Save context
                </Button>
              </form>
              <div className="flex gap-2">
                <Button
                  busy={busy === 'status'}
                  onClick={() =>
                    run('status', async () => {
                      await api(harness(`/projects/${selected}`), {
                        method: 'PATCH',
                        body: { status: current.status === 'active' ? 'paused' : 'active' },
                      });
                      await Promise.all([detail.reload(), projects.reload()]);
                    })
                  }
                >
                  {current.status === 'active' ? 'Pause project' : 'Resume project'}
                </Button>
              </div>
              <div className="rounded-lg border border-line p-3">
                <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                  <h3 className="text-sm font-medium">Research plan</h3>
                  <Button
                    busy={busy === 'draft-plan'}
                    disabled={current.status !== 'active' || currentPlan?.status === 'running'}
                    onClick={() =>
                      run(
                        'draft-plan',
                        async () => {
                          await api(harness(`/projects/${selected}/plan`), { method: 'POST' });
                          await plan.reload();
                        },
                        'Plan drafted for review.',
                      )
                    }
                  >
                    Draft plan
                  </Button>
                </div>
                {!currentPlan ? (
                  <p className="text-xs text-muted">
                    Draft a sequence of read-only research tasks. Review it before PANINI starts.
                  </p>
                ) : (
                  <>
                    <Badge
                      tone={
                        currentPlan.status === 'completed'
                          ? 'ok'
                          : currentPlan.status === 'needs_review'
                            ? 'warn'
                            : 'copper'
                      }
                    >
                      {currentPlan.status}
                    </Badge>
                    <ol className="mt-3 list-inside list-decimal space-y-2 text-sm">
                      {currentPlan.steps.map((step: any, index: number) => (
                        <li key={index}>
                          <span className="font-medium">{step.title}</span>
                          <p className="ml-5 text-xs text-muted">{step.goal}</p>
                        </li>
                      ))}
                    </ol>
                    {currentPlan.status === 'draft' && (
                      <Button
                        className="mt-3"
                        tone="primary"
                        busy={busy === 'approve-plan'}
                        onClick={() =>
                          run(
                            'approve-plan',
                            async () => {
                              await api(harness(`/projects/${selected}/plan/approve`), {
                                body: { revision: currentPlan.revision },
                              });
                              await Promise.all([plan.reload(), detail.reload()]);
                            },
                            'Plan approved. The first research task is queued.',
                          )
                        }
                      >
                        Approve and start
                      </Button>
                    )}
                  </>
                )}
              </div>
              <form
                className="grid gap-2"
                onSubmit={(event) => {
                  event.preventDefault();
                  void run(
                    'add-task',
                    async () => {
                      await api(harness('/tasks'), {
                        body: { goal: taskGoal, project_id: selected },
                      });
                      setTaskGoal('');
                      await detail.reload();
                    },
                    'Task added.',
                  );
                }}
              >
                <Field label="New background task">
                  <textarea
                    className={inputClass}
                    required
                    rows={2}
                    value={taskGoal}
                    onChange={(e) => setTaskGoal(e.target.value)}
                  />
                </Field>
                <Button
                  type="submit"
                  tone="primary"
                  disabled={current.status !== 'active'}
                  busy={busy === 'add-task'}
                >
                  Add task
                </Button>
              </form>
              <div>
                <h3 className="mb-2 text-sm font-medium">Tasks</h3>
                {(current.tasks ?? []).length === 0 ? (
                  <Empty>No tasks yet.</Empty>
                ) : (
                  <ul className="grid gap-2">
                    {current.tasks.map((task: any) => (
                      <li key={task.id} className="rounded-lg border border-line p-3">
                        <Badge tone={STATUS_TONE[task.status] ?? 'neutral'}>{task.status}</Badge>
                        <span className="ml-2 text-sm">{task.title}</span>
                        {task.result && (
                          <p className="mt-2 text-xs text-muted">{task.result.message}</p>
                        )}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </div>
          )}
        </Card>
      </div>
    </Page>
  );
}

export function MemoryPage() {
  const [query, setQuery] = useState('');
  const [search, setSearch] = useState('');
  const memories = useResource<any>(
    harness(`/memories${search ? `?q=${encodeURIComponent(search)}` : ''}`),
  );
  const [text, setText] = useState('');
  const [kind, setKind] = useState('preference');
  const { busy, run } = useAction();

  return (
    <Page
      title="Memory"
      deva="स्मृति"
      eyebrow="Anuvritti · what carries over"
      actions={
        <>
          <Button
            onClick={() =>
              run('export', async () => {
                const data = await api(harness('/memories/export'));
                const link = document.createElement('a');
                link.href = URL.createObjectURL(
                  new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }),
                );
                link.download = 'panini-memories.json';
                link.click();
                URL.revokeObjectURL(link.href);
              })
            }
          >
            <Download size={14} /> Export
          </Button>
          <Button
            tone="danger"
            onClick={() => {
              if (!confirm('Forget every memory PANINI has about you? This cannot be undone.'))
                return;
              void run(
                'forget-all',
                async () => {
                  await api(harness('/memories'), { method: 'DELETE' });
                  await memories.reload();
                },
                'Forgot everything.',
              );
            }}
          >
            <Trash2 size={14} /> Forget everything
          </Button>
        </>
      }
    >
      <div className="grid gap-5">
        <Card title="Teach PANINI something" deva="शिक्षा">
          <form
            className="grid gap-3 sm:grid-cols-[160px_1fr_auto]"
            onSubmit={(e) => {
              e.preventDefault();
              void run(
                'add',
                async () => {
                  await api(harness('/memories'), { body: { text, kind } });
                  setText('');
                  await memories.reload();
                },
                'Remembered.',
              );
            }}
          >
            <select
              className={inputClass}
              value={kind}
              onChange={(e) => setKind(e.target.value)}
              aria-label="Kind"
            >
              <option value="preference">Preference</option>
              <option value="profile">About me</option>
              <option value="fact">Fact</option>
              <option value="episode">Event</option>
            </select>
            <input
              className={inputClass}
              required
              maxLength={2000}
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder="For example: I prefer short answers in the morning"
            />
            <Button tone="primary" type="submit" busy={busy === 'add'}>
              Remember
            </Button>
          </form>
          <p className="mt-2 text-xs text-faint">
            Stored only on this PC, used as context (never as instructions). Passwords and keys are
            refused.
          </p>
        </Card>
        <Card
          title="Memories"
          actions={
            <form
              className="flex gap-2"
              onSubmit={(e) => {
                e.preventDefault();
                setSearch(query.trim());
              }}
            >
              <input
                className={inputClass + ' w-56'}
                placeholder="Search by meaning"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                aria-label="Search memories"
              />
              <Button type="submit">Search</Button>
            </form>
          }
        >
          {(memories.data?.memories ?? []).length === 0 ? (
            <Empty>
              {search ? 'No matching memories.' : 'PANINI has not remembered anything yet.'}
            </Empty>
          ) : (
            <ul className="derive grid gap-2">
              {memories.data.memories.map((m: any) => (
                <li
                  key={m.id}
                  className="flex items-start gap-3 rounded-lg border border-line bg-obsidian/50 px-3.5 py-3"
                >
                  <Badge tone="copper">{m.kind}</Badge>
                  <div className="min-w-0 flex-1">
                    <p className="text-sm">{m.text}</p>
                    <p className="mono mt-1 text-[11px] text-faint">
                      from {m.source}
                      {m.created ? ` · ${ago(m.created)}` : ''}
                      {m.uses ? ` · used ${m.uses}×` : ''}
                    </p>
                  </div>
                  <Button
                    tone="ghost"
                    onClick={() => {
                      const next = prompt('Edit memory', m.text);
                      if (next && next.trim() !== m.text)
                        void run(
                          'edit' + m.id,
                          async () => {
                            await api(harness(`/memories/${m.id}`), {
                              method: 'PATCH',
                              body: { text: next.trim() },
                            });
                            await memories.reload();
                          },
                          'Updated.',
                        );
                    }}
                  >
                    Edit
                  </Button>
                  <Button
                    tone="ghost"
                    onClick={() =>
                      run('forget' + m.id, async () => {
                        await api(harness(`/memories/${m.id}`), { method: 'DELETE' });
                        await memories.reload();
                      })
                    }
                  >
                    Forget
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </Page>
  );
}

export function AutomationsPage() {
  const tasks = useResource<any>(harness('/tasks'), 10000);
  const [goal, setGoal] = useState('');
  const [delay, setDelay] = useState('0');
  const [every, setEvery] = useState('');
  const [condition, setCondition] = useState('');
  const { busy, run } = useAction();
  const active = (tasks.data?.tasks ?? []).filter((t: any) =>
    ['queued', 'running', 'needs_review'].includes(t.status),
  );

  return (
    <Page title="Automations" deva="नियम" eyebrow="Schedules and watches">
      <div className="grid gap-5 lg:grid-cols-[1fr_1fr]">
        <Card title="New automation" deva="रचना">
          <form
            className="grid gap-4"
            onSubmit={(e) => {
              e.preventDefault();
              void run(
                'create',
                async () => {
                  if (condition.trim() && !every) throw new Error('A watch needs “Repeat every”.');
                  await api(harness('/tasks'), {
                    body: {
                      goal,
                      delay_minutes: Number(delay) || 0,
                      every_minutes: every ? Number(every) : null,
                      condition: condition.trim() || null,
                    },
                  });
                  setGoal('');
                  setCondition('');
                  await tasks.reload();
                },
                condition.trim() ? 'Watch added.' : 'Automation added.',
              );
            }}
          >
            <Field label="What should PANINI do or check?">
              <textarea
                className={inputClass}
                rows={3}
                required
                value={goal}
                onChange={(e) => setGoal(e.target.value)}
              />
            </Field>
            <div className="grid grid-cols-2 gap-3">
              <Field label="Start in (minutes)">
                <input
                  className={inputClass}
                  type="number"
                  min={0}
                  max={10080}
                  value={delay}
                  onChange={(e) => setDelay(e.target.value)}
                />
              </Field>
              <Field label="Repeat every (minutes)" hint="1440 = daily. Leave empty to run once.">
                <input
                  className={inputClass}
                  type="number"
                  min={5}
                  max={10080}
                  value={every}
                  onChange={(e) => setEvery(e.target.value)}
                />
              </Field>
            </div>
            <Field
              label="Only tell me when… (optional)"
              hint="Makes it a watch: you are notified only when this becomes true, once per new finding."
            >
              <input
                className={inputClass}
                value={condition}
                onChange={(e) => setCondition(e.target.value)}
                placeholder="the price drops below ₹50,000"
              />
            </Field>
            <div>
              <Button tone="primary" type="submit" busy={busy === 'create'}>
                Add automation
              </Button>
            </div>
            <p className="text-xs text-faint">
              Background runs use read-only research. Anything that needs your approval waits in the
              Inbox.
            </p>
          </form>
        </Card>
        <Card title="Active" deva="सक्रिय">
          {active.length === 0 ? (
            <Empty>No active automations.</Empty>
          ) : (
            <ul className="derive grid gap-2">
              {active.map((t: any) => (
                <li key={t.id} className="rounded-lg border border-line bg-obsidian/50 px-3.5 py-3">
                  <div className="flex items-center gap-2">
                    <Badge tone={STATUS_TONE[t.status] ?? 'neutral'}>
                      {t.kind === 'watch' ? 'watch' : t.status}
                    </Badge>
                    <span className="flex-1 truncate text-sm">{t.title}</span>
                    <Button
                      tone="ghost"
                      onClick={() =>
                        run(
                          'cancel' + t.id,
                          async () => {
                            await api(harness(`/tasks/${t.id}/cancel`), { method: 'POST' });
                            await tasks.reload();
                          },
                          'Cancelled.',
                        )
                      }
                    >
                      Cancel
                    </Button>
                  </div>
                  {t.condition && (
                    <p className="mt-1 text-xs text-muted">Tell me when: {t.condition}</p>
                  )}
                  <p className="mono mt-1 text-[11px] text-faint">
                    {t.every_minutes ? `every ${t.every_minutes} min` : 'once'}
                    {t.next_run && t.status === 'queued' ? ` · next ${when(t.next_run)}` : ''}
                  </p>
                  {t.result && (
                    <p className="mt-2 rounded bg-zinc-2 px-2 py-1.5 text-xs text-slate/80">
                      {t.result.message}
                    </p>
                  )}
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </Page>
  );
}
