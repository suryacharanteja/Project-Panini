import { Circle, Square, Play, RefreshCw } from 'lucide-react';
import { useState } from 'react';
import { admin, api, harness, useResource } from '../api';
import { Badge, Button, Card, Field, inputClass, useAction } from './ui';

export function TeachPanel() {
  const state = useResource<any>(harness('/teach'), 2500);
  const [browser, setBrowser] = useState('managed');
  const [windows, setWindows] = useState<any[]>([]);
  const [window, setWindow] = useState('');
  const [applications, setApplications] = useState<any[]>([]);
  const [applicationId, setApplicationId] = useState('');
  const [desktopRecordingMode, setDesktopRecordingMode] = useState<'window' | 'open'>('window');
  const desktop = browser === 'desktop';
  const [draft, setDraft] = useState<any>(null);
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [expected, setExpected] = useState('');
  const [selected, setSelected] = useState<any>(null);
  const [inputs, setInputs] = useState<Record<string, string>>({});
  const [result, setResult] = useState<any>(null);
  const { busy, run } = useAction();
  const recording = ['recording', 'starting'].includes(state.data?.recording?.state);
  const refreshWindows = () =>
    run('windows', async () => {
      const choices = (await api(harness('/teach/windows'))).windows;
      setWindows(choices);
      setWindow((current) => (choices.some((w: any) => w.choice_id === current) ? current : ''));
    });
  const refreshApplications = () =>
    run('applications', async () => {
      const choices = (await api(harness('/teach/applications'))).applications;
      setApplications(choices);
      setApplicationId((current) =>
        choices.some((app: any) => app.application_id === current) ? current : '',
      );
    });
  const windowPicker = (application = '') => (
    <div className="flex flex-wrap items-center gap-3">
      <select
        aria-label={application ? 'Replay Windows window' : 'Recording Windows window'}
        className={inputClass + ' max-w-96'}
        value={window}
        disabled={recording || state.data?.replaying}
        onChange={(e) => setWindow(e.target.value)}
      >
        <option value="">Select a Windows window</option>
        {windows
          .filter((w) => !application || w.application.toLowerCase() === application.toLowerCase())
          .map((w) => (
            <option key={w.choice_id} value={w.choice_id}>
              {w.label} · {w.application}
            </option>
          ))}
      </select>
      <Button
        disabled={recording || state.data?.replaying || !state.data?.enabled}
        busy={busy === 'windows'}
        onClick={() => void refreshWindows()}
      >
        <RefreshCw size={12} /> Refresh windows
      </Button>
    </div>
  );
  const start = () =>
    run('record', async () => {
      await api(harness('/teach/record/start'), {
        body: desktop
          ? desktopRecordingMode === 'open'
            ? { kind: 'desktop', application_id: applicationId }
            : { kind: 'desktop', window }
          : { kind: 'browser', browser },
      });
      setDraft(null);
      await state.reload();
    });
  const stop = () =>
    run('stop', async () => {
      const stopped = await api(harness('/teach/stop'), { body: {} });
      if (stopped.replay?.steps) setDraft(stopped.replay);
      await state.reload();
    });
  const open = (skill: string) =>
    run('open', async () => {
      const full = await api(harness(`/teach/skills/${skill}`));
      setSelected(full);
      setWindow('');
      setInputs(
        Object.fromEntries(
          Object.entries(full.replay.inputs).map(([k, v]: [string, any]) => [k, v.default]),
        ),
      );
      setResult(null);
    });

  return (
    <Card
      title="Teach PANINI"
      className="mb-5"
      actions={
        <Badge tone={recording ? 'error' : 'neutral'}>
          {recording ? '● Recording' : state.data?.enabled ? 'Ready' : 'Setup needed'}
        </Badge>
      }
    >
      <p className="mb-4 text-sm text-muted">
        Show a browser or Windows task once, review its inputs and expected result, then replay it.
        Each action follows your current permissions.
      </p>
      {!state.data?.enabled && (
        <p className="mb-4 text-xs text-copper-light">
          Enable Teach, the action ledger and durable reviews in Settings. Windows recording also
          needs fine desktop tools and an allowed application.
        </p>
      )}
      {state.error && (
        <p role="alert" className="mb-3 text-danger">
          {state.error}
        </p>
      )}
      <div className="flex flex-wrap items-center gap-3">
        <select
          className={inputClass + ' max-w-64'}
          aria-label="Recording source"
          value={browser}
          disabled={recording || state.data?.replaying}
          onChange={(e) => setBrowser(e.target.value)}
        >
          <option value="managed">PANINI browser</option>
          <option value="chrome">My attached Chrome tab</option>
          <option value="desktop">Windows application</option>
        </select>
        <Button
          tone="primary"
          disabled={
            !state.data?.enabled ||
            recording ||
            state.data?.replaying ||
            (desktop && (desktopRecordingMode === 'open' ? !applicationId : !window))
          }
          busy={busy === 'record'}
          onClick={() => void start()}
        >
          <Circle size={14} /> Watch me
        </Button>
        <Button
          tone="danger"
          disabled={!recording && !state.data?.replaying}
          busy={busy === 'stop'}
          onClick={() => void stop()}
        >
          <Square size={14} /> Stop
        </Button>
        {recording && (
          <span className="text-xs text-muted" role="status">
            Do the task in the selected{' '}
            {state.data?.recording?.kind === 'desktop' ? 'Windows window' : 'browser'}, then press
            Stop. Stop before entering credentials.
          </span>
        )}
      </div>
      {desktop && (
        <div className="mt-3 grid gap-3">
          <select
            aria-label="Windows recording mode"
            className={inputClass + ' max-w-96'}
            value={desktopRecordingMode}
            disabled={recording || state.data?.replaying}
            onChange={(e) => setDesktopRecordingMode(e.target.value as 'window' | 'open')}
          >
            <option value="window">Record in an open window</option>
            <option value="open">Open an installed app, then record</option>
          </select>
          {desktopRecordingMode === 'window' ? (
            windowPicker()
          ) : (
            <div className="flex flex-wrap items-center gap-3">
              <select
                aria-label="Application to open for recording"
                className={inputClass + ' max-w-96'}
                value={applicationId}
                disabled={recording || state.data?.replaying}
                onChange={(e) => setApplicationId(e.target.value)}
              >
                <option value="">Select an installed application</option>
                {applications.map((app) => (
                  <option key={app.application_id} value={app.application_id}>
                    {app.label}
                  </option>
                ))}
              </select>
              <Button
                disabled={recording || state.data?.replaying || !state.data?.enabled}
                busy={busy === 'applications'}
                onClick={() => void refreshApplications()}
              >
                <RefreshCw size={12} /> Refresh applications
              </Button>
            </div>
          )}
        </div>
      )}
      {state.data?.recording?.application && recording && (
        <p className="mt-2 text-xs text-copper-light" role="status">
          Recording {state.data.recording.application} · ends after 15 minutes.
        </p>
      )}
      {state.data?.recordings?.some((r: any) => r.state === 'draft') && !draft && (
        <div className="mt-3 flex flex-wrap gap-2">
          {state.data.recordings
            .filter((r: any) => r.state === 'draft')
            .slice(0, 5)
            .map((r: any) => (
              <Button
                key={r.id}
                onClick={() =>
                  void run('draft', async () =>
                    setDraft((await api(harness(`/teach/recordings/${r.id}`))).replay),
                  )
                }
              >
                Open recording · {new Date(r.created * 1000).toLocaleString()}
              </Button>
            ))}
        </div>
      )}
      {draft && (
        <form
          className="mt-5 grid gap-4 border-t border-line pt-5"
          onSubmit={(e) => {
            e.preventDefault();
            void run(
              'review',
              async () => {
                const replay = {
                  ...draft,
                  expect: [
                    {
                      id: 's999',
                      tool:
                        draft.kind === 'desktop'
                          ? 'desktop_verify_text'
                          : 'browser_verify_text_visible',
                      args: { text: expected },
                    },
                  ],
                };
                await api(harness('/teach/propose'), {
                  body: { name, description, replay, base_version: 0 },
                });
                setDraft(null);
                await state.reload();
              },
              'Skill review is ready in Inbox.',
            );
          }}
        >
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Skill name">
              <input
                required
                pattern="[a-z0-9][a-z0-9-]{1,62}"
                className={inputClass}
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="my-weekly-report"
              />
            </Field>
            <Field label="What does this task do?">
              <input
                required
                maxLength={300}
                className={inputClass}
                value={description}
                onChange={(e) => setDescription(e.target.value)}
              />
            </Field>
          </div>
          {draft.kind === 'desktop' && (
            <p className="text-xs text-muted">Reviewed application: {draft.desktop_application}</p>
          )}
          <ol className="grid gap-2" aria-label="Recorded steps">
            {draft.steps.map((s: any, i: number) => (
              <li
                key={s.id}
                className="flex flex-wrap items-center gap-2 rounded-lg border border-line p-3 text-xs"
              >
                <span className="mono text-copper-light">
                  {i + 1}. {s.tool.replace(/^(browser|desktop)_/, '')}
                </span>
                <span>{s.target?.name ?? s.args?.url ?? s.args?.application_id ?? s.note}</span>
                {s.target?.ancestors?.length > 0 && (
                  <span className="text-muted">
                    in {s.target.ancestors.map((p: any) => p.name).join(' → ')}
                  </span>
                )}
                {s.tool === 'manual' && <Badge tone="error">Needs correction</Badge>}
                <Button
                  className="ml-auto"
                  tone="ghost"
                  onClick={() =>
                    setDraft({
                      ...draft,
                      steps: draft.steps.filter((_: any, n: number) => n !== i),
                      recording_artifacts: Object.fromEntries(
                        Object.entries(draft.recording_artifacts ?? {}).filter(
                          ([key]) => key !== s.id,
                        ),
                      ),
                    })
                  }
                >
                  Remove
                </Button>
              </li>
            ))}
          </ol>
          <div className="grid gap-3 sm:grid-cols-2">
            {Object.entries(draft.inputs).map(([key, spec]: [string, any]) => (
              <Field
                key={key}
                label={`Input: ${key}`}
                hint={`Suggested ${spec.kind}. Review the default.`}
              >
                <input
                  className={inputClass}
                  value={spec.default}
                  onChange={(e) =>
                    setDraft({
                      ...draft,
                      inputs: { ...draft.inputs, [key]: { ...spec, default: e.target.value } },
                    })
                  }
                />
              </Field>
            ))}
          </div>
          <Field
            label="What visible text proves the task worked?"
            hint="Use a specific result, such as Report saved. A dispatched click alone is not proof."
          >
            <input
              required
              maxLength={500}
              className={inputClass}
              value={expected}
              onChange={(e) => setExpected(e.target.value)}
            />
          </Field>
          <details>
            <summary className="cursor-pointer text-xs text-muted">
              Advanced: edit normalized steps and input names
            </summary>
            <textarea
              key={JSON.stringify(draft)}
              aria-label="Normalized recording"
              className={inputClass + ' mono mt-3 min-h-64 text-xs'}
              defaultValue={JSON.stringify(draft, null, 2)}
              onBlur={(e) => {
                void run('edit', async () => setDraft(JSON.parse(e.target.value)));
              }}
            />
          </details>
          <Button
            type="submit"
            tone="primary"
            busy={busy === 'review'}
            disabled={!draft.steps.length || draft.steps.some((s: any) => s.tool === 'manual')}
          >
            Review skill in Inbox
          </Button>
        </form>
      )}
      <div className="mt-5 grid gap-2">
        {state.data?.skills?.map((skill: any) => (
          <div key={skill.name} className="rounded-lg border border-line p-3">
            <div className="flex flex-wrap items-center gap-2">
              <strong className="mono text-sm text-copper-glow">{skill.name}</strong>
              {skill.needs_attention && <Badge tone="error">Needs attention</Badge>}
              <span className="text-xs text-muted">
                {skill.passes}/{skill.runs} passed · {skill.heals} repairs
              </span>
              <Button
                className="ml-auto"
                disabled={!skill.versions.some((v: any) => v.state === 'approved')}
                onClick={() => void open(skill.name)}
              >
                <Play size={13} /> Open replay
              </Button>
              {skill.versions.some((v: any) => v.state === 'approved') && (
                <Button
                  tone="ghost"
                  onClick={() =>
                    void run(
                      'delete',
                      async () => {
                        await api(admin(`/skills/${skill.name}`), { method: 'DELETE' });
                        setSelected(null);
                        await state.reload();
                      },
                      'Skill deleted; its reviewed version history is retained.',
                    )
                  }
                >
                  Delete skill
                </Button>
              )}
            </div>
            <p className="mt-1 text-xs text-muted">
              {skill.versions.map((v: any) => `v${v.version} ${v.state}`).join(' · ')}
            </p>
            {skill.last_failure && <p className="mt-2 text-xs text-danger">{skill.last_failure}</p>}
          </div>
        ))}
      </div>
      {selected && (
        <div className="mt-4 grid gap-3 rounded-lg border border-copper/30 p-4">
          <h3 className="text-sm">
            Replay {selected.name} · v{selected.version}
          </h3>
          {selected.replay.kind === 'desktop' &&
            selected.replay.steps[0]?.tool !== 'desktop_open_application' &&
            windowPicker(selected.replay.desktop_application)}
          {selected.replay.steps[0]?.tool === 'desktop_open_application' && (
            <p className="text-xs text-muted">
              Replay will ask to open {selected.replay.steps[0].args.application_id} and verify a
              new window.
            </p>
          )}
          {Object.keys(inputs).map((key) => (
            <Field key={key} label={key}>
              <input
                className={inputClass}
                value={inputs[key]}
                onChange={(e) => setInputs({ ...inputs, [key]: e.target.value })}
              />
            </Field>
          ))}
          <p className="text-xs text-muted">
            Required action approvals appear in Inbox. Keep Stop available while replaying.
          </p>
          <Button
            tone="primary"
            disabled={
              !state.data?.enabled ||
              recording ||
              state.data?.replaying ||
              (selected.replay.kind === 'desktop' &&
                selected.replay.steps[0]?.tool !== 'desktop_open_application' &&
                !windows.some(
                  (w) =>
                    w.choice_id === window &&
                    w.application.toLowerCase() ===
                      selected.replay.desktop_application.toLowerCase(),
                ))
            }
            busy={busy === 'replay'}
            onClick={() =>
              void run('replay', async () => {
                setResult(
                  await api(harness('/teach/run'), {
                    body: {
                      name: selected.name,
                      inputs,
                      browser: desktop ? 'managed' : browser,
                      window:
                        selected.replay.kind === 'desktop' &&
                        selected.replay.steps[0]?.tool !== 'desktop_open_application'
                          ? window
                          : '',
                    },
                  }),
                );
                await state.reload();
              })
            }
          >
            <Play size={14} /> Replay skill
          </Button>
          {result && (
            <p role="status" className="text-sm">
              {result.state ?? result.error} {result.failure}
            </p>
          )}
        </div>
      )}
      <Button tone="ghost" className="mt-3" onClick={() => void state.reload()}>
        <RefreshCw size={12} /> Refresh
      </Button>
    </Card>
  );
}
