import { useState } from 'react';
import { admin, api, harness, useResource } from '../api';
import { Badge, Button, Card, Empty, Field, inputClass, useAction } from '../components/ui';

type Source = 'files' | 'email' | 'calendar' | 'webhook' | 'os' | 'schedule';
type Rule = { id: string; name: string; source: Source; status: string; review_id: string };
type Responsibility = {
  id: string;
  title: string;
  status: string;
  health: Record<string, number | string>;
  report_page_id?: string;
  space_id?: string;
};
type State = {
  enabled: boolean;
  needs_one_brain: boolean;
  rules: Rule[];
  responsibilities: Responsibility[];
  bindings: { source: string; server: string; tool: string; last_error?: string }[];
};

const KINDS: Record<Source, string[]> = {
  files: ['created', 'modified', 'deleted'],
  email: ['received'],
  calendar: ['starting_soon', 'changed', 'cancelled'],
  webhook: ['received'],
  os: ['resumed', 'locked', 'unlocked', 'on_battery', 'on_ac', 'online', 'offline'],
  schedule: ['fired', 'completed', 'failed'],
};
const TEMPLATES = {
  downloads: {
    title: 'Keep Downloads tidy',
    charter: 'Review new files in Downloads and keep an organized summary.',
    success: 'New files are classified and anything uncertain is flagged for me.',
    cadence: '1440',
    escalation: 'Ask before deleting or moving files.',
    hint: 'Add a reviewed file rule for your Downloads folder.',
  },
  invoices: {
    title: 'File invoices',
    charter: 'Track invoices from my chosen folder and email source.',
    success: 'Each new invoice is summarized with vendor, date and amount.',
    cadence: '1440',
    escalation: 'Ask before payment or any external message.',
    hint: 'Add reviewed file and email rules first.',
  },
  flight: {
    title: 'Watch my flight',
    charter: 'Watch for changes to my flight plans.',
    success: 'Summarize verified changes and raise cancellations promptly.',
    cadence: '60',
    escalation: 'Tell me as soon as a flight is cancelled.',
    hint: 'Add a reviewed email rule and a check source.',
  },
  meeting: {
    title: 'Prepare for meetings',
    charter: 'Review my upcoming meetings and relevant context.',
    success: 'A short preparation brief is ready before a meeting.',
    cadence: '60',
    escalation: 'Ask when a meeting has no clear agenda.',
    hint: 'Bind a read only calendar source and add a reviewed rule.',
  },
};

export function TriggerPanel() {
  const state = useResource<State>(harness('/triggers'), 10000);
  const people = useResource<{ personas: { id: string; name: string }[] }>(admin('/personas'));
  const { busy, run } = useAction();
  const [source, setSource] = useState<Source>('files');
  const [bindingSource, setBindingSource] = useState<'email' | 'calendar'>('email');
  const [kind, setKind] = useState('created');
  const [name, setName] = useState('');
  const [persona, setPersona] = useState('panini');
  const [goal, setGoal] = useState('');
  const [folder, setFolder] = useState('');
  const [pattern, setPattern] = useState('');
  const [filter, setFilter] = useState('');
  const [condition, setCondition] = useState('');
  const [budget, setBudget] = useState('0.05');
  const [dailyBudget, setDailyBudget] = useState('1.00');
  const [cooldown, setCooldown] = useState('60');
  const [limit, setLimit] = useState('20');
  const [charter, setCharter] = useState('');
  const [responsibilityTitle, setResponsibilityTitle] = useState('');
  const [success, setSuccess] = useState('');
  const [escalation, setEscalation] = useState('');
  const [cadence, setCadence] = useState('1440');
  const [report, setReport] = useState<'daily' | 'weekly'>('weekly');
  const [templateHint, setTemplateHint] = useState('');
  const [selected, setSelected] = useState<string[]>([]);
  const [server, setServer] = useState('');
  const [tool, setTool] = useState('');
  const [hook, setHook] = useState('');
  const [hookCredentials, setHookCredentials] = useState<{
    path: string;
    signing_secret: string;
  } | null>(null);

  const refresh = () => state.reload();
  const ruleMatch = () => {
    const match: Record<string, unknown> = { kind: [kind] };
    if (source === 'files') {
      match.watch_root = folder;
      match.path_glob = pattern;
    } else if (source === 'email' && filter) match.subject_contains = filter;
    else if (source === 'calendar' && filter) match.title_contains = filter;
    else if (source === 'webhook') match.hook = filter;
    return match;
  };

  return (
    <section className="mt-6 grid gap-5" aria-label="Event rules and responsibilities">
      <Card title="Event rules" deva="संकेत">
        <p className="mb-4 text-sm text-muted">
          Rules start only after you approve their exact goal and limits in Inbox.
          {!state.data?.enabled && ' Event rules are paused in Settings.'}
          {state.data?.needs_one_brain && ' Enable the v2 run engine first.'}
        </p>
        <form
          className="grid gap-3 md:grid-cols-2"
          onSubmit={(e) => {
            e.preventDefault();
            void run(
              'rule',
              async () => {
                await api(harness('/triggers'), {
                  body: {
                    name,
                    persona,
                    source,
                    match: ruleMatch(),
                    condition: condition.trim() || null,
                    action: { goal, budget_usd: Number(budget) },
                    cooldown_s: Number(cooldown),
                    max_per_day: Number(limit),
                    quiet_hours: null,
                  },
                });
                setName('');
                setGoal('');
                await refresh();
              },
              'Rule proposed. Review it in Inbox.',
            );
          }}
        >
          <Field label="Rule name">
            <input
              className={inputClass}
              required
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </Field>
          <Field label="Agent">
            <select
              className={inputClass}
              value={persona}
              onChange={(e) => setPersona(e.target.value)}
            >
              {(people.data?.personas ?? [{ id: 'panini', name: 'PANINI' }]).map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
            </select>
          </Field>
          <Field label="When">
            <select
              className={inputClass}
              value={source}
              onChange={(e) => {
                const next = e.target.value as Source;
                setSource(next);
                setKind(KINDS[next][0] ?? 'created');
              }}
            >
              {Object.keys(KINDS).map((item) => (
                <option key={item} value={item}>
                  {item}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Event">
            <select className={inputClass} value={kind} onChange={(e) => setKind(e.target.value)}>
              {KINDS[source].map((item) => (
                <option key={item}>{item}</option>
              ))}
            </select>
          </Field>
          {source === 'files' && (
            <>
              <Field label="Watched folder">
                <input
                  className={inputClass}
                  required
                  value={folder}
                  onChange={(e) => setFolder(e.target.value)}
                  placeholder="C:\\Users\\you\\Downloads"
                />
              </Field>
              <Field label="Absolute file pattern">
                <input
                  className={inputClass}
                  required
                  value={pattern}
                  onChange={(e) => setPattern(e.target.value)}
                  placeholder="C:\\Users\\you\\Downloads\\*invoice*.pdf"
                />
              </Field>
            </>
          )}
          {['email', 'calendar', 'webhook'].includes(source) && (
            <Field label={source === 'webhook' ? 'Webhook name' : 'Text contains (optional)'}>
              <input
                className={inputClass}
                required={source === 'webhook'}
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
              />
            </Field>
          )}
          <Field label="Optional condition">
            <input
              className={inputClass}
              value={condition}
              onChange={(e) => setCondition(e.target.value)}
              placeholder="Only when this is urgent"
            />
          </Field>
          <div className="md:col-span-2">
            <Field label="What should PANINI do?">
              <textarea
                className={inputClass}
                rows={3}
                required
                value={goal}
                onChange={(e) => setGoal(e.target.value)}
              />
            </Field>
          </div>
          <Field label="Budget per run, USD">
            <input
              className={inputClass}
              type="number"
              min="0.01"
              max="0.20"
              step="0.01"
              value={budget}
              onChange={(e) => setBudget(e.target.value)}
            />
          </Field>
          <Field label="Cooldown, seconds">
            <input
              className={inputClass}
              type="number"
              min="0"
              max="86400"
              value={cooldown}
              onChange={(e) => setCooldown(e.target.value)}
            />
          </Field>
          <Field label="Maximum per day">
            <input
              className={inputClass}
              type="number"
              min="1"
              max="200"
              value={limit}
              onChange={(e) => setLimit(e.target.value)}
            />
          </Field>
          <div className="md:col-span-2">
            <Button tone="primary" type="submit" busy={busy === 'rule'}>
              Propose rule
            </Button>
          </div>
        </form>
        <ul className="mt-5 grid gap-2">
          {(state.data?.rules ?? []).map((item) => (
            <li
              key={item.id}
              className="flex items-center gap-3 rounded-lg border border-line px-3 py-2 text-sm"
            >
              <Badge>{item.status}</Badge>
              <span className="flex-1">
                {item.name} · {item.source}
              </span>
              {item.status === 'proposed' && (
                <a className="text-copper" href="/inbox">
                  Review
                </a>
              )}
              {item.status === 'active' && (
                <Button
                  tone="ghost"
                  onClick={() =>
                    void run(
                      'pause-' + item.id,
                      async () => {
                        await api(harness(`/triggers/${item.id}/pause`), { method: 'POST' });
                        await refresh();
                      },
                      'Rule paused.',
                    )
                  }
                >
                  Pause
                </Button>
              )}
              {item.status === 'paused' && (
                <Button
                  tone="ghost"
                  onClick={() =>
                    void run(
                      'resume-' + item.id,
                      async () => {
                        await api(harness(`/triggers/${item.id}/resume`), { method: 'POST' });
                        await refresh();
                      },
                      'Rule resumed.',
                    )
                  }
                >
                  Resume
                </Button>
              )}
              {item.status !== 'retired' && (
                <Button
                  tone="ghost"
                  onClick={() =>
                    void run(
                      'retire-' + item.id,
                      async () => {
                        await api(harness(`/triggers/${item.id}/retire`), { method: 'POST' });
                        await refresh();
                      },
                      'Rule retired.',
                    )
                  }
                >
                  Retire
                </Button>
              )}
            </li>
          ))}
          {!state.data?.rules.length && <Empty>No event rules yet.</Empty>}
        </ul>
      </Card>
      <Card title="Responsibilities" deva="दायित्व">
        <p className="mb-4 text-sm text-muted">
          Give PANINI a reviewed charter, check cadence and report. Each check has its own budget.
        </p>
        <Field label="Start from an example">
          <select
            className={inputClass}
            defaultValue=""
            onChange={(e) => {
              const example = TEMPLATES[e.target.value as keyof typeof TEMPLATES];
              if (!example) return;
              setResponsibilityTitle(example.title);
              setCharter(example.charter);
              setSuccess(example.success);
              setCadence(example.cadence);
              setEscalation(example.escalation);
              setTemplateHint(example.hint);
            }}
          >
            <option value="">Choose a template</option>
            {Object.entries(TEMPLATES).map(([key, item]) => (
              <option value={key} key={key}>
                {item.title}
              </option>
            ))}
          </select>
        </Field>
        {templateHint && <p className="my-2 text-xs text-muted">{templateHint}</p>}
        <form
          className="grid gap-3 md:grid-cols-2"
          onSubmit={(e) => {
            e.preventDefault();
            void run(
              'responsibility',
              async () => {
                await api(harness('/triggers/responsibilities'), {
                  body: {
                    title: responsibilityTitle,
                    persona_id: persona,
                    charter,
                    success_criteria: success,
                    cadence_minutes: cadence ? Number(cadence) : null,
                    trigger_ids: selected,
                    report,
                    escalation,
                    space_id: null,
                    budget_usd: Number(budget),
                    daily_budget_usd: Number(dailyBudget),
                  },
                });
                setResponsibilityTitle('');
                setCharter('');
                setSuccess('');
                await refresh();
              },
              'Responsibility proposed. Review it in Inbox.',
            );
          }}
        >
          <Field label="Title">
            <input
              className={inputClass}
              required
              value={responsibilityTitle}
              onChange={(e) => setResponsibilityTitle(e.target.value)}
            />
          </Field>
          <Field label="Check every (minutes)">
            <input
              className={inputClass}
              type="number"
              min="5"
              max="10080"
              value={cadence}
              onChange={(e) => setCadence(e.target.value)}
            />
          </Field>
          <Field label="Report">
            <select
              className={inputClass}
              value={report}
              onChange={(e) => setReport(e.target.value as 'daily' | 'weekly')}
            >
              <option value="daily">Daily</option>
              <option value="weekly">Weekly</option>
            </select>
          </Field>
          <Field label="Maximum per day, USD">
            <input
              className={inputClass}
              type="number"
              min="0.05"
              max="20"
              step="0.05"
              value={dailyBudget}
              onChange={(e) => setDailyBudget(e.target.value)}
            />
          </Field>
          <div className="md:col-span-2">
            <Field label="Charter">
              <textarea
                className={inputClass}
                required
                rows={2}
                value={charter}
                onChange={(e) => setCharter(e.target.value)}
              />
            </Field>
          </div>
          <Field label="Success means">
            <input
              className={inputClass}
              required
              value={success}
              onChange={(e) => setSuccess(e.target.value)}
            />
          </Field>
          <Field label="When to involve me">
            <input
              className={inputClass}
              required
              value={escalation}
              onChange={(e) => setEscalation(e.target.value)}
            />
          </Field>
          {(state.data?.rules ?? []).filter((item) => item.status === 'active').length > 0 && (
            <fieldset className="md:col-span-2">
              <legend className="mb-1 text-sm">Include event rules</legend>
              {(state.data?.rules ?? [])
                .filter((item) => item.status === 'active')
                .map((item) => (
                  <label key={item.id} className="mr-4 text-sm">
                    <input
                      type="checkbox"
                      checked={selected.includes(item.id)}
                      onChange={(e) =>
                        setSelected(
                          e.target.checked
                            ? [...selected, item.id]
                            : selected.filter((id) => id !== item.id),
                        )
                      }
                    />{' '}
                    {item.name}
                  </label>
                ))}
            </fieldset>
          )}
          <div className="md:col-span-2">
            <Button tone="primary" type="submit" busy={busy === 'responsibility'}>
              Propose responsibility
            </Button>
          </div>
        </form>
        <ul className="mt-5 grid gap-2">
          {(state.data?.responsibilities ?? []).map((item) => (
            <li
              key={item.id}
              className="flex items-center gap-3 rounded-lg border border-line px-3 py-2 text-sm"
            >
              <Badge>{item.status}</Badge>
              <span className="flex-1">
                {item.title} · {item.health.successes ?? 0} successful checks
              </span>
              {item.report_page_id && item.space_id && (
                <a
                  className="text-copper"
                  href={`/spaces/${item.space_id}/pages/${item.report_page_id}`}
                >
                  Latest report
                </a>
              )}
              {item.status === 'active' && (
                <Button
                  tone="ghost"
                  onClick={() =>
                    void run(
                      'pause-resp-' + item.id,
                      async () => {
                        await api(harness(`/triggers/responsibilities/${item.id}/pause`), {
                          method: 'POST',
                        });
                        await refresh();
                      },
                      'Paused.',
                    )
                  }
                >
                  Pause
                </Button>
              )}
              {item.status === 'paused' && (
                <Button
                  tone="ghost"
                  onClick={() =>
                    void run(
                      'resume-resp-' + item.id,
                      async () => {
                        await api(harness(`/triggers/responsibilities/${item.id}/resume`), {
                          method: 'POST',
                        });
                        await refresh();
                      },
                      'Resumed.',
                    )
                  }
                >
                  Resume
                </Button>
              )}
            </li>
          ))}
        </ul>
      </Card>
      <Card title="Connected sources and webhooks" deva="स्रोत">
        <p className="mb-4 text-sm text-muted">
          Email and calendar need a configured read only connector tool and your background access
          grant.
        </p>
        <form
          className="flex flex-wrap gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            void run(
              'binding',
              async () => {
                await api(harness('/triggers/bindings'), {
                  body: { source: bindingSource, server, tool, arguments: {} },
                });
                await refresh();
              },
              'Source connected.',
            );
          }}
        >
          <select
            className={inputClass}
            value={bindingSource}
            onChange={(e) => setBindingSource(e.target.value as 'email' | 'calendar')}
          >
            <option>email</option>
            <option>calendar</option>
          </select>
          <input
            className={inputClass}
            required
            placeholder="Connector"
            value={server}
            onChange={(e) => setServer(e.target.value)}
          />
          <input
            className={inputClass}
            required
            placeholder="Read only tool"
            value={tool}
            onChange={(e) => setTool(e.target.value)}
          />
          <Button type="submit" busy={busy === 'binding'}>
            Bind
          </Button>
        </form>
        {(state.data?.bindings ?? []).map((item) => (
          <p className="mt-2 text-xs" key={item.source}>
            {item.source}: {item.server}.{item.tool}
            {item.last_error && ` · ${item.last_error}`}
          </p>
        ))}
        <form
          className="mt-5 flex flex-wrap gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            void run(
              'hook',
              async () => {
                setHookCredentials(await api(harness('/triggers/hooks'), { body: { name: hook } }));
              },
              'Webhook created. Save its secret now.',
            );
          }}
        >
          <input
            className={inputClass}
            required
            placeholder="Webhook name"
            value={hook}
            onChange={(e) => setHook(e.target.value)}
          />
          <Button type="submit" busy={busy === 'hook'}>
            Create signed webhook
          </Button>
        </form>
        {hookCredentials && (
          <div className="mt-3 rounded-lg border border-line p-3 text-xs">
            <p>
              Path: <code>{hookCredentials.path}</code>
            </p>
            <p>
              Signing secret (shown once): <code>{hookCredentials.signing_secret}</code>
            </p>
          </div>
        )}
      </Card>
    </section>
  );
}
