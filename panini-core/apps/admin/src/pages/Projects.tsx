import { Link } from '@tanstack/react-router';
import { ArrowRight, Check, CirclePause, Plus, RefreshCw } from 'lucide-react';
import { useEffect, useState } from 'react';
import { ago, api, harness, useResource } from '../api';
import { Badge, Button, Card, Empty, Field, inputClass, Page, useAction } from '../components/ui';
import { isVerified, taskCounts, type ProjectTask } from './project-state';

type Project = {
  id: string;
  title: string;
  goal: string;
  context: string;
  status: 'active' | 'paused' | 'completed';
  updated: number;
  tasks?: ProjectTask[];
};
type Step = { title: string; goal: string; success_criteria?: string; task_id: string | null };
type Plan = {
  project_id: string;
  revision: number;
  review_reason?: string;
  status: 'draft' | 'running' | 'needs_review' | 'completed' | 'cancelled';
  steps: Step[];
};

function taskTone(task: ProjectTask) {
  const status = task.status;
  if (status === 'completed') return isVerified(task) ? ('ok' as const) : ('warn' as const);
  if (status === 'failed' || status === 'cancelled') return 'error' as const;
  if (status === 'needs_review') return 'warn' as const;
  if (status === 'running') return 'active' as const;
  return 'neutral' as const;
}

function taskLabel(task: ProjectTask) {
  return task.status === 'completed' && !isVerified(task) ? 'completed · unverified' : task.status;
}

function PlanView({ plan, tasks }: { plan: Plan; tasks: ProjectTask[] }) {
  const completed = plan.steps.filter((step) => {
    const task = tasks.find((item) => item.id === step.task_id);
    return isVerified(task);
  }).length;
  return (
    <div className="grid gap-4">
      <div className="flex flex-wrap items-center gap-3">
        <Badge
          tone={
            plan.status === 'completed' ? 'ok' : plan.status === 'needs_review' ? 'warn' : 'copper'
          }
        >
          {plan.status.replace('_', ' ')}
        </Badge>
        <span className="text-xs text-muted">
          Revision {plan.revision} · {completed} of {plan.steps.length} verified
        </span>
      </div>
      <div
        role="progressbar"
        aria-label="Verified plan steps"
        aria-valuemin={0}
        aria-valuemax={plan.steps.length}
        aria-valuenow={completed}
        className="h-1.5 overflow-hidden rounded-full bg-zinc-3"
      >
        <div
          className="h-full rounded-full bg-copper transition-all"
          style={{ width: `${(completed / plan.steps.length) * 100}%` }}
        />
      </div>
      <ol className="grid gap-2">
        {plan.steps.map((step, index) => {
          const task = tasks.find((item) => item.id === step.task_id);
          const verified = isVerified(task);
          return (
            <li
              key={`${plan.revision}-${index}`}
              className="flex gap-3 rounded-xl border border-line bg-obsidian/45 p-3.5"
            >
              <span className="mono mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-full border border-copper/35 text-xs text-copper-light">
                {verified ? <Check size={14} /> : index + 1}
              </span>
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-sm font-medium">{step.title}</span>
                  <Badge tone={task ? taskTone(task) : 'neutral'}>
                    {task ? taskLabel(task) : 'up next'}
                  </Badge>
                </div>
                <p className="mt-1 text-xs leading-relaxed text-muted">{step.goal}</p>
                {step.success_criteria && (
                  <p className="mt-2 text-xs leading-relaxed text-slate/80">
                    <span className="font-medium">Success criteria:</span> {step.success_criteria}
                  </p>
                )}
                {task?.result?.message && (
                  <p className="mt-2 whitespace-pre-wrap text-xs text-slate/85">
                    {task.result.message}
                  </p>
                )}
                {verified && (
                  <p className="mono mt-2 text-[11px] text-gold">
                    Verified with {task?.result?.evidence?.length} evidence item
                    {task?.result?.evidence?.length === 1 ? '' : 's'}
                  </p>
                )}
                {step.task_id && (
                  <Link
                    to="/runs"
                    className="mt-2 inline-flex items-center gap-1 text-xs text-copper-light hover:text-copper-glow"
                  >
                    View activity <ArrowRight size={12} />
                  </Link>
                )}
              </div>
            </li>
          );
        })}
      </ol>
      {plan.status === 'needs_review' && (
        <p
          role="alert"
          className="rounded-lg border border-amber/30 bg-amber/5 p-3 text-sm text-amber"
        >
          {plan.review_reason ??
            'PANINI stopped this plan because a step could not be verified. Review the result before drafting a new plan.'}
        </p>
      )}
    </div>
  );
}

export function ProjectsPage() {
  const projects = useResource<{ projects: Project[] }>(harness('/projects'), 8000);
  const [selected, setSelected] = useState<string | null>(() =>
    sessionStorage.getItem('panini:selected-project'),
  );
  const [creating, setCreating] = useState(false);
  const detail = useResource<Project>(selected ? harness(`/projects/${selected}`) : null, 8000);
  const plan = useResource<{ plan: Plan | null }>(
    selected ? harness(`/projects/${selected}/plan`) : null,
    8000,
  );
  const current = detail.data?.id === selected ? detail.data : null;
  const currentPlan = plan.data?.plan?.project_id === selected ? plan.data.plan : null;
  const [title, setTitle] = useState('');
  const [goal, setGoal] = useState('');
  const [context, setContext] = useState('');
  const [editContext, setEditContext] = useState('');
  const [taskGoal, setTaskGoal] = useState('');
  const { busy, run } = useAction();
  const selectProject = (id: string) => {
    sessionStorage.setItem('panini:selected-project', id);
    setSelected(id);
  };

  useEffect(() => {
    const available = projects.data?.projects;
    if (available?.[0] && !available.some((project) => project.id === selected)) {
      sessionStorage.setItem('panini:selected-project', available[0].id);
      setSelected(available[0].id);
    }
  }, [projects.data, selected]);
  useEffect(() => setEditContext(current?.context ?? ''), [current?.id, current?.updated]);

  const refresh = () => Promise.all([projects.reload(), detail.reload(), plan.reload()]);
  const counts = taskCounts(current?.tasks ?? []);

  return (
    <Page
      title="Projects"
      deva="योजना"
      eyebrow="Goals, progress and decisions"
      actions={
        <Button tone="primary" onClick={() => setCreating((value) => !value)}>
          <Plus size={15} /> New project
        </Button>
      }
    >
      <div className="grid gap-5 lg:grid-cols-[minmax(240px,0.72fr)_minmax(0,1.6fr)]">
        <div className="grid content-start gap-5">
          {creating && (
            <Card title="Start a project">
              <form
                className="grid gap-3"
                onSubmit={(event) => {
                  event.preventDefault();
                  void run(
                    'create-project',
                    async () => {
                      const created = await api<Project>(harness('/projects'), {
                        body: { title, goal, context },
                      });
                      setTitle('');
                      setGoal('');
                      setContext('');
                      setCreating(false);
                      await projects.reload();
                      selectProject(created.id);
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
                    onChange={(event) => setTitle(event.target.value)}
                  />
                </Field>
                <Field label="Goal">
                  <textarea
                    className={inputClass}
                    required
                    maxLength={4000}
                    rows={3}
                    value={goal}
                    onChange={(event) => setGoal(event.target.value)}
                  />
                </Field>
                <Field label="Context for future tasks">
                  <textarea
                    className={inputClass}
                    maxLength={12000}
                    rows={3}
                    value={context}
                    onChange={(event) => setContext(event.target.value)}
                  />
                </Field>
                <Button type="submit" tone="primary" busy={busy === 'create-project'}>
                  Create project
                </Button>
              </form>
            </Card>
          )}
          <Card
            title="Your projects"
            actions={
              <span className="mono text-xs text-faint">{projects.data?.projects.length ?? 0}</span>
            }
          >
            {projects.error && (
              <p role="alert" className="text-sm text-danger">
                {projects.error}
              </p>
            )}
            {!projects.loading && !projects.data?.projects.length ? (
              <Empty>No projects yet. Create one to keep a goal and its work together.</Empty>
            ) : (
              <ul className="grid gap-2">
                {projects.data?.projects.map((project) => (
                  <li key={project.id}>
                    <button
                      type="button"
                      aria-current={selected === project.id ? 'true' : undefined}
                      onClick={() => selectProject(project.id)}
                      className={`w-full rounded-xl border p-3.5 text-left transition ${selected === project.id ? 'border-copper/70 bg-copper/10' : 'border-line bg-obsidian/40 hover:border-copper/40'}`}
                    >
                      <div className="flex items-center justify-between gap-2">
                        <span className="truncate text-sm font-medium">{project.title}</span>
                        <Badge tone={project.status === 'active' ? 'ok' : 'neutral'}>
                          {project.status}
                        </Badge>
                      </div>
                      <p className="mt-1 line-clamp-2 text-xs leading-relaxed text-muted">
                        {project.goal}
                      </p>
                      <p className="mono mt-2 text-[11px] text-faint">
                        Updated {ago(project.updated)}
                      </p>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </div>
        {!selected ? (
          <Card>
            <Empty>Select a project or create one to begin.</Empty>
          </Card>
        ) : !current ? (
          <Card>
            {detail.error ? (
              <p role="alert" className="text-sm text-danger">
                {detail.error}
              </p>
            ) : (
              <p className="text-sm text-muted">Loading project…</p>
            )}
          </Card>
        ) : (
          <div className="grid content-start gap-5">
            <Card
              title={current.title}
              actions={
                <Badge tone={current.status === 'active' ? 'ok' : 'neutral'}>
                  {current.status}
                </Badge>
              }
            >
              <p className="whitespace-pre-wrap text-sm leading-relaxed text-slate/90">
                {current.goal}
              </p>
              <div className="mt-5 grid grid-cols-3 gap-2 border-t border-line pt-4 text-center">
                <div>
                  <div className="text-xl text-gold">{counts.verified}</div>
                  <div className="text-xs text-muted">Verified</div>
                </div>
                <div>
                  <div className="text-xl text-copper-light">{counts.working}</div>
                  <div className="text-xs text-muted">In progress</div>
                </div>
                <div>
                  <div className="text-xl text-amber">{counts.attention}</div>
                  <div className="text-xs text-muted">Need review</div>
                </div>
              </div>
              <div className="mt-4 flex flex-wrap gap-2">
                <Button
                  busy={busy === 'status'}
                  onClick={() =>
                    void run('status', async () => {
                      await api(harness(`/projects/${selected}`), {
                        method: 'PATCH',
                        body: { status: current.status === 'active' ? 'paused' : 'active' },
                      });
                      await refresh();
                    })
                  }
                >
                  {current.status === 'active' ? (
                    <CirclePause size={14} />
                  ) : (
                    <RefreshCw size={14} />
                  )}
                  {current.status === 'active' ? 'Pause project' : 'Resume project'}
                </Button>
                <Link
                  to="/runs"
                  className="inline-flex min-h-9 items-center gap-1 rounded-lg px-3 text-sm text-copper-light hover:text-copper-glow"
                >
                  All activity <ArrowRight size={14} />
                </Link>
              </div>
            </Card>
            <Card
              title="Research plan"
              actions={
                <div className="flex flex-wrap gap-2">
                  {currentPlan && !['completed', 'cancelled'].includes(currentPlan.status) && (
                    <Button
                      tone="danger"
                      busy={busy === 'cancel-plan'}
                      onClick={() =>
                        void run(
                          'cancel-plan',
                          async () => {
                            await api(harness(`/projects/${selected}/plan/cancel`), {
                              body: { revision: currentPlan.revision },
                            });
                            await refresh();
                          },
                          'Plan stopped. Its unfinished tasks are cancelled.',
                        )
                      }
                    >
                      Stop plan
                    </Button>
                  )}
                  <Button
                    busy={busy === 'draft-plan'}
                    disabled={current.status !== 'active' || currentPlan?.status === 'running'}
                    onClick={() =>
                      void run(
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
              }
            >
              <p className="mb-4 text-xs leading-relaxed text-muted">
                PANINI proposes read-only steps using web research and app tools you granted for
                background work. Review the sequence and success criteria before starting. Each next
                step waits for verified evidence.
              </p>
              {plan.error && (
                <p role="alert" className="text-sm text-danger">
                  {plan.error}
                </p>
              )}
              {!currentPlan ? (
                <Empty>No plan yet.</Empty>
              ) : (
                <PlanView plan={currentPlan} tasks={current.tasks ?? []} />
              )}
              {currentPlan?.status === 'draft' && (
                <Button
                  className="mt-4"
                  tone="primary"
                  busy={busy === 'approve-plan'}
                  onClick={() =>
                    void run(
                      'approve-plan',
                      async () => {
                        await api(harness(`/projects/${selected}/plan/approve`), {
                          body: { revision: currentPlan.revision },
                        });
                        await refresh();
                      },
                      'Plan approved. The first research task is queued.',
                    )
                  }
                >
                  Approve revision {currentPlan.revision} and start
                </Button>
              )}
            </Card>
            <Card
              title="Tasks"
              actions={<Badge tone="neutral">{current.tasks?.length ?? 0}</Badge>}
            >
              {!current.tasks?.length ? (
                <Empty>No tasks yet.</Empty>
              ) : (
                <ul className="grid gap-2">
                  {current.tasks.map((task) => (
                    <li key={task.id} className="rounded-lg border border-line bg-obsidian/45 p-3">
                      <div className="flex flex-wrap items-center gap-2">
                        <Badge tone={taskTone(task)}>{taskLabel(task)}</Badge>
                        <span className="text-sm">{task.title}</span>
                      </div>
                      {task.result?.message && (
                        <p className="mt-2 whitespace-pre-wrap text-xs text-muted">
                          {task.result.message}
                        </p>
                      )}
                    </li>
                  ))}
                </ul>
              )}
              <form
                className="mt-4 grid gap-2 border-t border-line pt-4"
                onSubmit={(event) => {
                  event.preventDefault();
                  void run(
                    'add-task',
                    async () => {
                      await api(harness('/tasks'), {
                        body: { goal: taskGoal, project_id: selected },
                      });
                      setTaskGoal('');
                      await refresh();
                    },
                    'Task added.',
                  );
                }}
              >
                <Field label="Add a background research task">
                  <textarea
                    className={inputClass}
                    required
                    rows={2}
                    value={taskGoal}
                    onChange={(event) => setTaskGoal(event.target.value)}
                  />
                </Field>
                <div>
                  <Button
                    type="submit"
                    disabled={current.status !== 'active'}
                    busy={busy === 'add-task'}
                  >
                    Add task
                  </Button>
                </div>
              </form>
            </Card>
            <Card title="Project context">
              <p className="mb-3 text-xs text-muted">
                This context is carried into future work. Edit it when the goal or constraints
                change.
              </p>
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
                      await refresh();
                    },
                    'Project context saved.',
                  );
                }}
              >
                <Field label="Context">
                  <textarea
                    className={inputClass}
                    rows={4}
                    maxLength={12000}
                    value={editContext}
                    onChange={(event) => setEditContext(event.target.value)}
                  />
                </Field>
                <div>
                  <Button
                    type="submit"
                    busy={busy === 'save-context'}
                    disabled={editContext === current.context}
                  >
                    Save context
                  </Button>
                </div>
              </form>
            </Card>
          </div>
        )}
      </div>
    </Page>
  );
}
