import { useState } from 'react';
import { admin, api, useResource } from '../api';
import { Badge, Button, Card, Empty, inputClass, Page, useAction } from '../components/ui';

type Account = {
  id: string;
  label: string;
  enabled: boolean;
  token_configured: boolean;
  state: string;
  config: { owner_ids: string[]; group_ids: string[] };
};
type Status = {
  enabled_channels: string[];
  accounts: Account[];
  outbox: { id: string; state: string; error_class?: string }[];
};

export function ChannelsPage() {
  const resource = useResource<Status>(admin('/channels'), 3000);
  const { busy, run } = useAction();
  const [editing, setEditing] = useState<string | null>(null);
  const [label, setLabel] = useState('');
  const [owners, setOwners] = useState('');
  const [groups, setGroups] = useState('');
  const [token, setToken] = useState('');
  const [enabled, setEnabled] = useState(false);
  const ids = (text: string) => text.split(/[\s,]+/).filter(Boolean);
  const reset = () => {
    setEditing(null);
    setLabel('');
    setOwners('');
    setGroups('');
    setToken('');
    setEnabled(false);
  };
  return (
    <Page title="Messaging channels" eyebrow="Your conversations, where you are">
      <div className="grid gap-4">
        <Card title="Telegram">
          <p className="text-sm text-faint">
            PANINI accepts text from your explicit numeric user IDs. Work runs with background
            permissions; approvals stay on your PC or paired phone. Set PANINI_CHANNELS to include
            telegram in Settings.
          </p>
          {resource.error && <p role="alert">{resource.error}</p>}
          {!resource.data ? (
            <Empty>Loading accounts…</Empty>
          ) : (
            resource.data.accounts.map((account) => (
              <div className="mt-4 flex items-center justify-between gap-3" key={account.id}>
                <div>
                  <strong>{account.label}</strong> <Badge>{account.state}</Badge>
                  <p className="text-xs text-faint">
                    Owner IDs: {account.config.owner_ids.join(', ')} · Token{' '}
                    {account.token_configured ? 'saved' : 'missing'}
                  </p>
                </div>
                <Button
                  onClick={() => {
                    setEditing(account.id);
                    setLabel(account.label);
                    setOwners(account.config.owner_ids.join(', '));
                    setGroups(account.config.group_ids.join(', '));
                    setToken('');
                    setEnabled(account.enabled);
                  }}
                >
                  Edit
                </Button>
              </div>
            ))
          )}
          <form
            className="mt-5 grid gap-3"
            onSubmit={(event) => {
              event.preventDefault();
              void run('save', async () => {
                await api(admin(`/channels/telegram${editing ? `/${editing}` : ''}`), {
                  method: editing ? 'PUT' : 'POST',
                  body: {
                    label,
                    owner_ids: ids(owners),
                    group_ids: ids(groups),
                    enabled,
                    ...(token ? { token } : {}),
                  },
                });
                reset();
                resource.reload();
              });
            }}
          >
            <h3>{editing ? 'Edit account' : 'Add a Telegram bot'}</h3>
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
            <label>
              Owner user IDs
              <input
                className={inputClass}
                value={owners}
                required
                placeholder="Numeric IDs, separated by commas"
                onChange={(e) => setOwners(e.target.value)}
              />
            </label>
            <label>
              Allowed group IDs (optional)
              <input
                className={inputClass}
                value={groups}
                placeholder="Negative group IDs"
                onChange={(e) => setGroups(e.target.value)}
              />
            </label>
            <label>
              Bot token
              <input
                className={inputClass}
                type="password"
                autoComplete="new-password"
                value={token}
                onChange={(e) => setToken(e.target.value)}
                placeholder={
                  editing ? 'Leave blank to keep the saved token' : 'Token from BotFather'
                }
              />
            </label>
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
              {editing && <Button onClick={reset}>Cancel edit</Button>}
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
        <Card title="Further channel development">
          <p className="text-sm text-faint">
            Slack, WhatsApp, email, attachments and proactive delivery remain pending. Telegram
            live-provider qualification requires a configured bot and owner account.
          </p>
        </Card>
      </div>
    </Page>
  );
}
