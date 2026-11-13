import { useState } from 'react';
import { admin, api, useResource } from '../api';
import { Badge, Button, Card, Empty, inputClass, Page, useAction } from '../components/ui';

type Channel = 'telegram' | 'slack' | 'whatsapp' | 'email';
type Account = {
  id: string;
  channel: Channel;
  label: string;
  enabled: boolean;
  token_configured: boolean;
  secrets_configured?: Record<string, boolean>;
  state: string;
  config: Record<string, string | string[]> & { owner_ids: string[]; group_ids: string[] };
};
type Status = {
  enabled_channels: string[];
  accounts: Account[];
  outbox: { id: string; state: string; error_class?: string }[];
};
type Field = {
  key: string;
  label: string;
  placeholder?: string;
  secret?: boolean;
  list?: boolean;
  optional?: boolean;
};

const CHANNELS: Record<Channel, { title: string; about: string; fields: Field[] }> = {
  telegram: {
    title: 'Telegram',
    about:
      'Text from your explicit numeric Telegram user IDs, in private chats or allow-listed groups.',
    fields: [
      {
        key: 'owner_ids',
        label: 'Owner user IDs',
        placeholder: 'Numeric IDs, separated by commas',
        list: true,
      },
      {
        key: 'group_ids',
        label: 'Allowed group IDs',
        placeholder: 'Negative group IDs',
        list: true,
        optional: true,
      },
      { key: 'token', label: 'Bot token', placeholder: 'Token from BotFather', secret: true },
    ],
  },
  slack: {
    title: 'Slack',
    about:
      'Socket Mode (no public address). Answers your mentions in allowed channels and your direct messages; follow-ups only in threads you started with a mention.',
    fields: [
      { key: 'team_id', label: 'Workspace ID', placeholder: 'T…' },
      {
        key: 'owner_ids',
        label: 'Owner member IDs',
        placeholder: 'U…, separated by commas',
        list: true,
      },
      {
        key: 'group_ids',
        label: 'Allowed channel IDs',
        placeholder: 'C…, separated by commas',
        list: true,
        optional: true,
      },
      { key: 'token', label: 'Bot token', placeholder: 'xoxb-…', secret: true },
      {
        key: 'app_token',
        label: 'App-level token',
        placeholder: 'xapp-… (connections:write)',
        secret: true,
      },
    ],
  },
  whatsapp: {
    title: 'WhatsApp',
    about:
      'WhatsApp Cloud API. Messages arrive at PANINI’s verified webhook, so remote access (Funnel or the relay) must be on.',
    fields: [
      {
        key: 'owner_ids',
        label: 'Owner phone numbers',
        placeholder: 'International form, e.g. 447700900001',
        list: true,
      },
      {
        key: 'phone_number_id',
        label: 'Phone number ID',
        placeholder: 'From the Meta app dashboard',
      },
      { key: 'token', label: 'Access token', secret: true },
      { key: 'app_secret', label: 'App secret', secret: true },
      {
        key: 'verify_token',
        label: 'Verify token',
        placeholder: '16+ characters you choose',
        secret: true,
      },
    ],
  },
  email: {
    title: 'Email',
    about:
      'A dedicated mailbox. Only authenticated mail (DKIM or SPF aligned) from your addresses is answered; lists and auto-generated mail never are.',
    fields: [
      {
        key: 'owner_ids',
        label: 'Owner addresses',
        placeholder: 'you@example.com, separated by commas',
        list: true,
      },
      { key: 'address', label: 'PANINI’s address', placeholder: 'panini@example.org' },
      { key: 'username', label: 'Mailbox user name' },
      { key: 'imap_host', label: 'IMAP server', placeholder: 'imap.example.org' },
      { key: 'imap_port', label: 'IMAP port', placeholder: '993', optional: true },
      { key: 'smtp_host', label: 'SMTP server', placeholder: 'smtp.example.org' },
      { key: 'smtp_port', label: 'SMTP port', placeholder: '587 or 465', optional: true },
      { key: 'token', label: 'Mailbox password or app password', secret: true },
    ],
  },
};

const ids = (text: string) => text.split(/[\s,]+/).filter(Boolean);

export function ChannelsPage() {
  const resource = useResource<Status>(admin('/channels'), 3000);
  const { busy, run } = useAction();
  const [channel, setChannel] = useState<Channel>('telegram');
  const [editing, setEditing] = useState<string | null>(null);
  const [label, setLabel] = useState('');
  const [values, setValues] = useState<Record<string, string>>({});
  const [enabled, setEnabled] = useState(false);
  const spec = CHANNELS[channel];

  const reset = (next: Channel = channel) => {
    setChannel(next);
    setEditing(null);
    setLabel('');
    setValues({});
    setEnabled(false);
  };

  const edit = (account: Account) => {
    setChannel(account.channel);
    setEditing(account.id);
    setLabel(account.label);
    const next: Record<string, string> = {};
    for (const field of CHANNELS[account.channel].fields) {
      if (field.secret) continue;
      const value = account.config[field.key];
      next[field.key] = Array.isArray(value) ? value.join(', ') : String(value ?? '');
    }
    setValues(next);
    setEnabled(account.enabled);
  };

  const body = () => {
    const out: Record<string, unknown> = { label, enabled };
    for (const field of spec.fields) {
      const value = (values[field.key] ?? '').trim();
      if (field.secret) {
        if (value) out[field.key] = value; // Blank keeps the saved secret.
      } else out[field.key] = field.list ? ids(value) : value;
    }
    return out;
  };

  const accounts = resource.data?.accounts ?? [];
  return (
    <Page title="Messaging channels" eyebrow="Your conversations, where you are">
      <div className="grid gap-4">
        <Card title="Accounts">
          <p className="text-sm text-faint">
            PANINI answers only you, identified by provider IDs (never display names). Work runs
            with background permissions; approvals stay on your PC or paired phone, and channel
            messages can never approve. Turn channels on with PANINI_CHANNELS in Settings (for
            example telegram,slack,whatsapp,email).
          </p>
          {resource.error && <p role="alert">{resource.error}</p>}
          {!resource.data ? (
            <Empty>Loading accounts…</Empty>
          ) : accounts.length === 0 ? (
            <p className="mt-3 text-sm text-faint">No channel accounts yet.</p>
          ) : (
            accounts.map((account) => (
              <div className="mt-4 flex items-center justify-between gap-3" key={account.id}>
                <div>
                  <strong>{account.label}</strong>{' '}
                  <Badge>{CHANNELS[account.channel]?.title ?? account.channel}</Badge>{' '}
                  <Badge tone={account.state === 'ready' ? 'ok' : 'neutral'}>{account.state}</Badge>
                  <p className="text-xs text-faint">
                    Owners: {account.config.owner_ids.join(', ')} · Credentials{' '}
                    {account.token_configured &&
                    Object.values(account.secrets_configured ?? {}).every(Boolean)
                      ? 'saved'
                      : 'incomplete'}
                    {!account.enabled && ' · disabled'}
                    {account.enabled &&
                      !resource.data!.enabled_channels.includes(account.channel) &&
                      ' · not in PANINI_CHANNELS'}
                  </p>
                </div>
                <Button onClick={() => edit(account)}>Edit</Button>
              </div>
            ))
          )}
        </Card>
        <Card title={editing ? `Edit ${spec.title} account` : 'Add an account'}>
          {!editing && (
            <div className="mb-3 flex flex-wrap gap-2" role="tablist" aria-label="Channel">
              {(Object.keys(CHANNELS) as Channel[]).map((name) => (
                <Button
                  key={name}
                  tone={name === channel ? 'primary' : undefined}
                  onClick={() => reset(name)}
                >
                  {CHANNELS[name].title}
                </Button>
              ))}
            </div>
          )}
          <p className="text-sm text-faint">{spec.about}</p>
          <form
            className="mt-4 grid gap-3"
            onSubmit={(event) => {
              event.preventDefault();
              void run('save', async () => {
                await api(admin(`/channels/${channel}${editing ? `/${editing}` : ''}`), {
                  method: editing ? 'PUT' : 'POST',
                  body: body(),
                });
                reset();
                resource.reload();
              });
            }}
          >
            <label>
              Name
              <input
                className={inputClass}
                value={label}
                maxLength={80}
                required
                onChange={(e) => setLabel(e.target.value)}
              />
            </label>
            {spec.fields.map((field) => (
              <label key={field.key}>
                {field.label}
                {field.optional && ' (optional)'}
                <input
                  className={inputClass}
                  type={field.secret ? 'password' : 'text'}
                  autoComplete={field.secret ? 'new-password' : 'off'}
                  value={values[field.key] ?? ''}
                  required={!field.optional && !field.secret}
                  placeholder={
                    field.secret && editing
                      ? 'Leave blank to keep the saved value'
                      : field.placeholder
                  }
                  onChange={(e) => setValues({ ...values, [field.key]: e.target.value })}
                />
              </label>
            ))}
            <label>
              <input
                type="checkbox"
                checked={enabled}
                onChange={(e) => setEnabled(e.target.checked)}
              />{' '}
              Enable this account
            </label>
            <p className="text-xs text-faint">
              Credentials are stored in the Windows user vault and never displayed again. Saving
              access changes stops unauthorized queued work.
            </p>
            <div className="flex gap-2">
              <Button type="submit" busy={busy === 'save'}>
                Save account
              </Button>
              {editing && <Button onClick={() => reset()}>Cancel edit</Button>}
            </div>
          </form>
        </Card>
        <Card title="Delivery">
          <p className="text-sm text-faint">
            Uncertain sends are retained for review and are not automatically repeated.
          </p>
          {resource.data?.outbox
            .filter((item) => ['unknown', 'queued'].includes(item.state))
            .map((item) => (
              <p key={item.id} className="mt-2 text-sm">
                {item.state} {item.error_class ? `· ${item.error_class}` : ''}
              </p>
            ))}
        </Card>
      </div>
    </Page>
  );
}
