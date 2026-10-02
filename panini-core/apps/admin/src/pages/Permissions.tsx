import { Pause, Play, Trash2 } from 'lucide-react';
import { useState } from 'react';
import { api, harness, useResource } from '../api';
import { Badge, Button, Card, Empty, Field, inputClass, Page, useAction } from '../components/ui';

type Grant = {
  id: string;
  subject: string;
  capability: string;
  tools: string[];
  levels: Record<string, string>;
  expires_at: number | null;
  note: string;
};
type PolicyView = {
  mode: string;
  profile: string;
  tiers: string[];
  matrix: Record<string, string[]>;
  grants: Grant[];
  paused: boolean;
  shadow: { more_permissive: number; stricter: number };
};

const LEVEL_TONE: Record<string, 'ok' | 'warn' | 'error' | 'copper' | 'neutral'> = {
  none: 'ok',
  tap: 'copper',
  verify: 'warn',
  local: 'warn',
  deny: 'error',
};
const EFFECTS = [
  'observe',
  'navigate',
  'input',
  'local_write',
  'local_exec',
  'submit',
  'share_out',
  'communicate',
  'transact',
  'account',
  'destroy',
  'install',
];

/** What PANINI may do on its own, what it asks first, and from where (docs: V2-03 policy engine). */
export function PermissionsPage() {
  const policy = useResource<PolicyView>(harness('/policy'));
  const { busy, run } = useAction();
  const [draft, setDraft] = useState({
    subject: 'owner',
    tools: '*',
    effect: 'local_exec',
    level: 'tap',
  });

  const pause = (paused: boolean) =>
    run(
      'pause',
      async () => {
        await api(harness('/policy/pause'), { body: { paused } });
        await policy.reload();
      },
      paused ? 'PANINI paused: no actions until you resume' : 'PANINI resumed',
    );
  const add = () =>
    run(
      'add',
      async () => {
        await api(harness('/policy/grants'), {
          body: {
            subject: draft.subject.trim(),
            tools: draft.tools
              .split(',')
              .map((t) => t.trim())
              .filter(Boolean),
            levels: { [draft.effect]: draft.level },
          },
        });
        await policy.reload();
      },
      'Permission added',
    );
  const revoke = (id: string) =>
    run(
      id,
      async () => {
        await api(harness(`/policy/grants/${id}`), { method: 'DELETE' });
        await policy.reload();
      },
      'Permission removed',
    );

  const view = policy.data;
  return (
    <Page
      title="Permissions"
      deva="अनुमति"
      eyebrow="Policy"
      actions={
        view && (
          <Button
            tone={view.paused ? 'primary' : 'danger'}
            busy={busy === 'pause'}
            onClick={() => pause(!view.paused)}
          >
            {view.paused ? <Play size={14} /> : <Pause size={14} />}{' '}
            {view.paused ? 'Resume PANINI' : 'Pause PANINI'}
          </Button>
        )
      }
    >
      {!view && <Empty>{policy.error || 'Loading permissions…'}</Empty>}
      {view && (
        <div className="grid gap-5">
          <Card title="How PANINI decides">
            <p className="text-sm text-muted">
              Profile <Badge tone="copper">{view.profile}</Badge> · engine{' '}
              <Badge tone={view.mode === 'enforce' ? 'ok' : 'neutral'}>{view.mode}</Badge>
              {view.mode === 'shadow' && (
                <>
                  {' '}
                  — deciding alongside the v1 rules without changing them:{' '}
                  {view.shadow.more_permissive} looser, {view.shadow.stricter} stricter so far.
                </>
              )}
            </p>
            <div className="mt-4 overflow-x-auto">
              <table className="w-full text-left text-xs">
                <thead>
                  <tr className="text-faint">
                    <th className="py-1 pr-3 font-normal">Effect</th>
                    {view.tiers.map((t) => (
                      <th key={t} className="py-1 pr-3 font-normal capitalize">
                        {t}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(view.matrix).map(([effect, row]) => (
                    <tr key={effect} className="border-t border-line/60">
                      <td className="mono py-1.5 pr-3 text-slate">{effect.replace('_', ' ')}</td>
                      {row.map((level, i) => (
                        <td key={i} className="py-1.5 pr-3">
                          <Badge tone={LEVEL_TONE[level] ?? 'neutral'}>{level}</Badge>
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>

          <Card title="Your permissions">
            {view.grants.length === 0 && (
              <Empty>No custom permissions. The profile above applies.</Empty>
            )}
            <ul className="grid gap-2">
              {view.grants.map((g) => (
                <li key={g.id} className="flex flex-wrap items-center gap-2 text-sm">
                  <span className="mono text-slate">{g.subject}</span>
                  <span className="text-faint">{g.tools.join(', ')}</span>
                  {Object.entries(g.levels).map(([effect, level]) => (
                    <Badge key={effect} tone={LEVEL_TONE[level] ?? 'neutral'}>
                      {effect}: {level}
                    </Badge>
                  ))}
                  {g.expires_at && (
                    <span className="text-xs text-faint">
                      until {new Date(g.expires_at * 1000).toLocaleString()}
                    </span>
                  )}
                  <Button
                    tone="ghost"
                    aria-label="Remove permission"
                    busy={busy === g.id}
                    onClick={() => revoke(g.id)}
                  >
                    <Trash2 size={14} />
                  </Button>
                </li>
              ))}
            </ul>
            <div className="mt-4 grid gap-3 sm:grid-cols-4">
              <Field label="Who">
                <input
                  className={inputClass}
                  value={draft.subject}
                  onChange={(e) => setDraft({ ...draft, subject: e.target.value })}
                />
              </Field>
              <Field label="Tools" hint="Comma list; * for all">
                <input
                  className={inputClass}
                  value={draft.tools}
                  onChange={(e) => setDraft({ ...draft, tools: e.target.value })}
                />
              </Field>
              <Field label="Effect">
                <select
                  className={inputClass}
                  value={draft.effect}
                  onChange={(e) => setDraft({ ...draft, effect: e.target.value })}
                >
                  {EFFECTS.map((e) => (
                    <option key={e}>{e}</option>
                  ))}
                </select>
              </Field>
              <Field label="Level">
                <select
                  className={inputClass}
                  value={draft.level}
                  onChange={(e) => setDraft({ ...draft, level: e.target.value })}
                >
                  {['none', 'tap', 'verify', 'local', 'deny'].map((l) => (
                    <option key={l}>{l}</option>
                  ))}
                </select>
              </Field>
            </div>
            <div className="mt-3">
              <Button tone="primary" busy={busy === 'add'} onClick={add}>
                Add permission
              </Button>
            </div>
          </Card>
        </div>
      )}
    </Page>
  );
}
