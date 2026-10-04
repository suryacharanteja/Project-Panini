import { useEffect, useState } from 'react';
import { api, harness, useResource } from '../api';
import { Badge, Button, Card, inputClass, useAction } from '../components/ui';

const acknowledgements = {
  personal_tasks_only: 'Personal tasks only; no marketing or bulk calling.',
  ai_disclosure: 'PANINI must disclose that it is an AI calling on my behalf.',
  local_rules_and_no_recording:
    'I am responsible for local automated-call rules. Audio recording stays off.',
  calling_hours_and_blocks: 'Calls must respect calling hours and blocked numbers.',
  approve_every_brief: 'I approve every exact call brief before dialing.',
};
const commitments = [
  'confirm a new appointment time',
  'confirm a reservation',
  'request a callback',
];
const emptyBrief = {
  callee_name: '',
  number: '',
  number_source: 'owner',
  purpose: '',
  owner_first_name: '',
  may_disclose: '',
  acceptable_outcomes: '',
  may_commit: [] as string[],
  max_minutes: 10,
  fallback: 'ask_callback',
  voicemail: 'leave_message',
  language: 'en',
};
type Brief = typeof emptyBrief;
type Row = {
  id: string;
  state: string;
  brief: any;
  reason?: string;
  receipt_id?: string;
  outcome?: { summary: string };
};
type Status = {
  enabled: boolean;
  checklist_signed: boolean;
  signed_at?: number;
  credentials_saved: boolean;
  config: { provider_url?: string; caller_number?: string; owner_number?: string };
  briefs: Row[];
  active?: { id: string; transcript: { role: string; text: string }[] };
  blocked: { number: string }[];
};

export function OutboundPanel() {
  const resource = useResource<Status>(harness('/outbound'), 3000);
  const { busy, run } = useAction();
  const [config, setConfig] = useState({ provider_url: '', caller_number: '', owner_number: '' });
  const [loaded, setLoaded] = useState(false);
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [checked, setChecked] = useState<Record<string, boolean>>({});
  const [brief, setBrief] = useState<Brief>(emptyBrief);
  const [editing, setEditing] = useState<string | null>(null);
  const [note, setNote] = useState('');
  const [blockedNumber, setBlockedNumber] = useState('');
  useEffect(() => {
    if (!loaded && resource.data) {
      setConfig({ provider_url: '', caller_number: '', owner_number: '', ...resource.data.config });
      setLoaded(true);
    }
  }, [resource.data, loaded]);
  const act = (key: string, path: string, body = {}) =>
    run(key, async () => {
      await api(harness(path), { method: 'POST', body });
      resource.reload();
    });
  const data = resource.data;
  if (!loaded)
    return (
      <Card title="Outbound calls">
        <p>{resource.error || 'Loading outbound setup…'}</p>
      </Card>
    );
  return (
    <>
      <Card
        title={
          <span>
            Outbound calls <Badge>{data?.enabled ? 'ready' : 'off'}</Badge>
          </span>
        }
      >
        <p className="text-sm text-faint">
          Every call needs a reviewed brief in Inbox. No automatic retries. Enable
          PANINI_OUTBOUND_CALLS in Settings after configuring your trunk and acknowledging the
          checklist. Live SIP qualification remains pending.
        </p>
        {resource.error && <p role="alert">{resource.error}</p>}
        <form
          className="mt-4 grid gap-3"
          onSubmit={(event) => {
            event.preventDefault();
            void run('trunk', async () => {
              const allChecked = Object.keys(acknowledgements).every((key) => checked[key]);
              await api(harness('/outbound/settings'), {
                method: 'POST',
                body: {
                  config,
                  ...(username || password ? { username, password } : {}),
                  ...(allChecked ? { checklist: checked } : {}),
                },
              });
              setUsername('');
              setPassword('');
              setChecked({});
              resource.reload();
            });
          }}
        >
          {(['provider_url', 'caller_number', 'owner_number'] as const).map((key) => (
            <label key={key}>
              {
                {
                  provider_url: 'TLS SIP provider',
                  caller_number: 'Outbound caller number',
                  owner_number: 'Owner takeover number',
                }[key]
              }
              <input
                className={inputClass}
                value={config[key]}
                required
                onChange={(event) => setConfig({ ...config, [key]: event.target.value })}
                placeholder={
                  key === 'provider_url' ? 'sips:sip.provider.example:5061' : '+14155550123'
                }
              />
            </label>
          ))}
          <label>
            SIP username
            <input
              className={inputClass}
              value={username}
              autoComplete="off"
              onChange={(event) => setUsername(event.target.value)}
            />
          </label>
          <label>
            SIP password
            <input
              className={inputClass}
              type="password"
              value={password}
              autoComplete="new-password"
              onChange={(event) => setPassword(event.target.value)}
            />
          </label>
          <p className="text-xs text-faint">
            Credentials are encrypted for this Windows account. Leave both blank to keep them.{' '}
            {data?.credentials_saved ? 'Credentials saved.' : 'No credentials saved.'}
          </p>
          <fieldset className="grid gap-2">
            <legend className="mb-2">
              Owner checklist {data?.checklist_signed ? '· acknowledged' : '· required'}
            </legend>
            {Object.entries(acknowledgements).map(([key, label]) => (
              <label key={key}>
                <input
                  type="checkbox"
                  checked={!!checked[key]}
                  onChange={(event) => setChecked({ ...checked, [key]: event.target.checked })}
                />{' '}
                {label}
              </label>
            ))}
          </fieldset>
          <Button type="submit" busy={busy === 'trunk'} disabled={!!data?.active}>
            Save outbound setup
          </Button>
        </form>
      </Card>
      <Card title={editing ? 'Edit call brief' : 'Prepare one call'}>
        <p className="text-sm text-faint">
          Emergency, premium, service and unknown-timezone numbers are blocked. Calls outside
          09:00–20:00 require a fresh review during the permitted window. Give PANINI only the facts
          it may disclose.
        </p>
        <form
          className="mt-4 grid gap-3"
          onSubmit={(event) => {
            event.preventDefault();
            void run('brief', async () => {
              await api(harness(`/outbound/briefs${editing ? `/${editing}` : ''}`), {
                method: editing ? 'PUT' : 'POST',
                body: {
                  ...brief,
                  may_disclose: brief.may_disclose
                    .split('\n')
                    .map((s) => s.trim())
                    .filter(Boolean),
                  acceptable_outcomes: brief.acceptable_outcomes
                    .split('\n')
                    .map((s) => s.trim())
                    .filter(Boolean),
                },
              });
              setBrief(emptyBrief);
              setEditing(null);
              resource.reload();
            });
          }}
        >
          {(['callee_name', 'number', 'purpose', 'owner_first_name'] as const).map((key) => (
            <label key={key}>
              {
                {
                  callee_name: 'Callee name',
                  number: 'Destination number',
                  purpose: 'Call purpose',
                  owner_first_name: 'Your first name',
                }[key]
              }
              <input
                className={inputClass}
                required
                value={brief[key]}
                maxLength={key === 'number' ? 16 : key === 'owner_first_name' ? 60 : 500}
                onChange={(event) => setBrief({ ...brief, [key]: event.target.value })}
              />
            </label>
          ))}
          <label>
            Number source
            <select
              className={inputClass}
              value={brief.number_source}
              onChange={(event) => setBrief({ ...brief, number_source: event.target.value })}
            >
              {['owner', 'contact', 'web', 'page', 'email'].map((source) => (
                <option key={source}>{source}</option>
              ))}
            </select>
          </label>
          <label>
            Facts PANINI may disclose (one per line)
            <textarea
              className={inputClass}
              value={brief.may_disclose}
              maxLength={10000}
              onChange={(event) => setBrief({ ...brief, may_disclose: event.target.value })}
            />
          </label>
          <label>
            Acceptable outcomes (one per line)
            <textarea
              className={inputClass}
              required
              value={brief.acceptable_outcomes}
              maxLength={10000}
              onChange={(event) => setBrief({ ...brief, acceptable_outcomes: event.target.value })}
            />
          </label>
          <fieldset className="grid gap-2">
            <legend>Permitted commitments</legend>
            {commitments.map((item) => (
              <label key={item}>
                <input
                  type="checkbox"
                  checked={brief.may_commit.includes(item)}
                  onChange={(event) =>
                    setBrief({
                      ...brief,
                      may_commit: event.target.checked
                        ? [...brief.may_commit, item]
                        : brief.may_commit.filter((value) => value !== item),
                    })
                  }
                />{' '}
                {item}
              </label>
            ))}
          </fieldset>
          <p className="text-xs text-faint">
            Payments, card details, government IDs, home addresses, passwords and codes remain
            excluded.
          </p>
          <label>
            Maximum minutes
            <input
              className={inputClass}
              type="number"
              min={1}
              max={30}
              value={brief.max_minutes}
              onChange={(event) => setBrief({ ...brief, max_minutes: Number(event.target.value) })}
            />
          </label>
          <label>
            If the owner is unavailable
            <select
              className={inputClass}
              value={brief.fallback}
              onChange={(event) => setBrief({ ...brief, fallback: event.target.value })}
            >
              {['ask_callback', 'take_message', 'end_politely'].map((value) => (
                <option key={value} value={value}>
                  {value.replaceAll('_', ' ')}
                </option>
              ))}
            </select>
          </label>
          <label>
            Voicemail
            <select
              className={inputClass}
              value={brief.voicemail}
              onChange={(event) => setBrief({ ...brief, voicemail: event.target.value })}
            >
              <option value="leave_message">Leave a brief message</option>
              <option value="hang_up">Hang up</option>
            </select>
          </label>
          <div className="flex gap-2">
            <Button type="submit" disabled={!data?.enabled} busy={busy === 'brief'}>
              {editing ? 'Replace review' : 'Send brief to Inbox'}
            </Button>
            {editing && (
              <Button
                onClick={() => {
                  setEditing(null);
                  setBrief(emptyBrief);
                }}
              >
                Cancel edit
              </Button>
            )}
          </div>
        </form>
      </Card>
      {data?.active && (
        <Card title="Outbound call in progress">
          <div className="flex flex-wrap gap-2">
            <Button onClick={() => act('end', `/outbound/${data.active!.id}/end`)}>
              End outbound call
            </Button>
            <Button onClick={() => act('transfer', `/outbound/${data.active!.id}/transfer`)}>
              Take over on my phone
            </Button>
          </div>
          <label>
            Owner note
            <input
              className={inputClass}
              value={note}
              maxLength={500}
              onChange={(event) => setNote(event.target.value)}
            />
          </label>
          <Button onClick={() => act('note', `/outbound/${data.active!.id}/note`, { text: note })}>
            Send note
          </Button>
          {data.active.transcript.map((message, index) => (
            <p className="mt-2 break-words text-sm" key={index}>
              {message.role}: {message.text}
            </p>
          ))}
        </Card>
      )}
      <Card title="Outbound briefs and receipts">
        {!data?.briefs.length && <p className="text-faint">No outbound briefs yet.</p>}
        {data?.briefs.map((row) => (
          <article key={row.id} className="mt-4 border-t border-line pt-3">
            <h3>
              {row.brief.callee_name} <Badge>{row.state}</Badge>
            </h3>
            <dl className="mt-2 grid gap-2 text-sm">
              {Object.entries(row.brief).map(([key, value]) => (
                <div key={key}>
                  <dt className="text-faint">{key.replaceAll('_', ' ')}</dt>
                  <dd className="break-words">
                    {Array.isArray(value) ? value.join('; ') || 'None' : String(value)}
                  </dd>
                </div>
              ))}
            </dl>
            {row.state === 'review' && (
              <Button
                onClick={() => {
                  setEditing(row.id);
                  setBrief({
                    ...row.brief,
                    may_disclose: row.brief.may_disclose.join('\n'),
                    acceptable_outcomes: row.brief.acceptable_outcomes.join('\n'),
                  });
                }}
              >
                Edit unapproved brief
              </Button>
            )}
            {row.reason && <p className="mt-2">{row.reason}</p>}
            {row.outcome && <p>{row.outcome.summary}</p>}
            {row.receipt_id && <p className="text-xs text-faint">Receipt {row.receipt_id}</p>}
          </article>
        ))}
      </Card>
      <Card title="Do not call">
        <form
          className="grid gap-3"
          onSubmit={(event) => {
            event.preventDefault();
            void act('block', '/outbound/block', { number: blockedNumber });
          }}
        >
          <label>
            Number to block
            <input
              className={inputClass}
              required
              value={blockedNumber}
              onChange={(event) => setBlockedNumber(event.target.value)}
            />
          </label>
          <Button type="submit" busy={busy === 'block'}>
            Block outbound number
          </Button>
        </form>
        {data?.blocked.map((row) => (
          <p key={row.number} className="mt-2">
            {row.number}
          </p>
        ))}
      </Card>
    </>
  );
}
