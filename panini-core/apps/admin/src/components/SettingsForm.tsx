import { CheckCircle2, FolderPlus, KeyRound, RotateCw, X } from 'lucide-react';
import { useMemo, useState } from 'react';
import { admin, api, useResource } from '../api';
import { Badge, Button, Card, cx, Field, inputClass, Switch, useAction } from './ui';

export type SettingRow = {
  key: string;
  group: string;
  label: string;
  type: 'text' | 'secret' | 'bool' | 'int' | 'enum' | 'paths';
  help?: string;
  default?: string;
  restart?: boolean;
  options?: string[];
  minimum?: number;
  maximum?: number;
  test?: string;
  advanced?: boolean;
  value?: any;
  configured?: boolean;
  masked?: string;
  source?: string;
  overridden?: boolean;
  restart_pending?: boolean;
};

export type SettingsView = {
  groups: Record<string, string>;
  settings: SettingRow[];
  restart_needed: string[];
};

const truthy = (v: unknown) => ['true', '1', 'yes', 'on'].includes(String(v ?? '').toLowerCase());

/** Rendered entirely from the backend's setting catalogue: a new setting needs no UI code. */
export function SettingsForm({
  groups,
  title,
  deva,
  description,
}: {
  groups: string[];
  title: string;
  deva?: string;
  description?: string;
}) {
  const { data, error, reload, setData } = useResource<SettingsView>(admin('/settings'));
  const [draft, setDraft] = useState<Record<string, any>>({});
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [saved, setSaved] = useState(0);
  const { busy, run } = useAction();

  const rows = useMemo(
    () => (data?.settings ?? []).filter((s) => groups.includes(s.group)),
    [data, groups],
  );
  const visible = rows.filter((r) => showAdvanced || !r.advanced);
  const dirty = Object.keys(draft).length > 0;

  const set = (key: string, value: any) =>
    setDraft((d) => {
      const next = { ...d };
      // A cancelled or empty key field never clears the saved key.
      if (value === undefined || (rows.find((r) => r.key === key)?.type === 'secret' && !value))
        delete next[key];
      else next[key] = value;
      return next;
    });
  const value = (row: SettingRow) => (row.key in draft ? draft[row.key] : row.value);

  async function save() {
    const ok = await run(
      'save',
      async () => {
        const next = await api<SettingsView>(admin('/settings'), {
          method: 'PATCH',
          body: { changes: draft },
        });
        setData(next);
        setDraft({});
        setSaved((n) => n + 1);
      },
      'Saved.',
    );
    if (!ok) void reload();
  }

  if (error)
    return (
      <Card title={title}>
        <p className="text-sm text-danger">{error}</p>
      </Card>
    );
  if (!data)
    return (
      <Card title={title}>
        <p className="text-sm text-faint">Loading…</p>
      </Card>
    );

  return (
    <Card
      key={saved}
      flash={saved > 0}
      title={title}
      deva={deva}
      actions={
        <>
          {rows.some((r) => r.advanced) && (
            <Button tone="ghost" onClick={() => setShowAdvanced((v) => !v)}>
              {showAdvanced ? 'Hide advanced' : 'Advanced'}
            </Button>
          )}
          {dirty && (
            <>
              <Button tone="ghost" onClick={() => setDraft({})}>
                Discard
              </Button>
              <Button tone="primary" busy={busy === 'save'} onClick={save}>
                Save changes
              </Button>
            </>
          )}
        </>
      }
    >
      {description && (
        <p className="-mt-2 mb-4 max-w-3xl text-sm leading-relaxed text-muted">{description}</p>
      )}
      <div className="derive grid gap-5">
        {visible.map((row) => (
          <SettingField
            key={row.key}
            row={row}
            value={value(row)}
            onChange={(v) => set(row.key, v)}
          />
        ))}
      </div>
    </Card>
  );
}

function SettingField({
  row,
  value,
  onChange,
}: {
  row: SettingRow;
  value: any;
  onChange: (v: any) => void;
}) {
  const badges = (
    <span className="ml-2 inline-flex gap-1.5 align-middle">
      {row.restart && (
        <Badge tone={row.restart_pending ? 'warn' : 'neutral'}>
          {row.restart_pending ? 'restart pending' : 'applies after restart'}
        </Badge>
      )}
      {row.overridden && <Badge tone="warn">overridden by environment</Badge>}
    </span>
  );
  const label = (
    <>
      {row.label}
      {badges}
    </>
  );
  const hint = row.help ? <Markdownish text={row.help} /> : undefined;

  if (row.type === 'secret') return <SecretField row={row} value={value} onChange={onChange} />;
  if (row.type === 'bool')
    return (
      <div className="flex items-start justify-between gap-6 border-b border-line/60 pb-4 last:border-0">
        <div className="grid gap-1 text-sm">
          <span>{label}</span>
          {hint && <span className="text-xs leading-relaxed text-faint">{hint}</span>}
        </div>
        <Switch checked={truthy(value)} onChange={(v) => onChange(v)} label={row.label} />
      </div>
    );
  if (row.type === 'enum')
    return (
      <Field label={label} hint={hint}>
        <select
          className={inputClass}
          value={value ?? ''}
          onChange={(e) => onChange(e.target.value)}
        >
          {(row.options ?? []).map((o) => (
            <option key={o} value={o}>
              {o || '(default)'}
            </option>
          ))}
        </select>
      </Field>
    );
  if (row.type === 'int')
    return (
      <Field label={label} hint={hint}>
        <input
          type="number"
          className={cx(inputClass, 'max-w-48')}
          min={row.minimum}
          max={row.maximum}
          value={value ?? ''}
          onChange={(e) => onChange(e.target.value)}
        />
      </Field>
    );
  if (row.type === 'paths')
    return <PathsField row={row} value={value ?? []} onChange={onChange} label={label} />;
  return (
    <Field label={label} hint={hint}>
      <input
        className={inputClass}
        value={value ?? ''}
        placeholder={row.default ? `Default: ${row.default}` : 'Default'}
        onChange={(e) => onChange(e.target.value)}
      />
    </Field>
  );
}

function SecretField({
  row,
  value,
  onChange,
}: {
  row: SettingRow;
  value: any;
  onChange: (v: any) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [result, setResult] = useState<{ ok: boolean; detail: string } | null>(null);
  const { busy, run } = useAction();
  return (
    <div className="grid gap-2 rounded-xl border border-line bg-obsidian/60 p-4">
      <div className="flex flex-wrap items-center gap-3">
        <KeyRound size={15} className="text-copper" />
        <span className="text-sm">{row.label}</span>
        {row.configured ? (
          <span className="mono text-xs text-muted">{row.masked}</span>
        ) : (
          <Badge tone="warn">not set</Badge>
        )}
        {result && (
          <Badge tone={result.ok ? 'ok' : 'error'}>{result.ok ? 'verified' : result.detail}</Badge>
        )}
        <div className="flex-1" />
        {row.test && row.configured && (
          <Button
            busy={busy === 'test'}
            onClick={() =>
              run('test', async () =>
                setResult(await api(admin(`/keys/${row.test}/test`), { method: 'POST' })),
              )
            }
          >
            <CheckCircle2 size={14} /> Test
          </Button>
        )}
        <Button
          onClick={() => (editing ? (setEditing(false), onChange(undefined)) : setEditing(true))}
        >
          {editing ? <X size={14} /> : <RotateCw size={14} />}{' '}
          {editing ? 'Cancel' : row.configured ? 'Replace' : 'Add key'}
        </Button>
      </div>
      {row.help && <p className="text-xs text-faint">{row.help}</p>}
      {editing && (
        <input
          type="password"
          autoComplete="off"
          className={inputClass}
          placeholder="Paste the new key. It is tested before it replaces the current one."
          value={value === undefined || typeof value !== 'string' ? '' : value}
          onChange={(e) => onChange(e.target.value)}
        />
      )}
    </div>
  );
}

function PathsField({
  row,
  value,
  onChange,
  label,
}: {
  row: SettingRow;
  value: string[];
  onChange: (v: string[]) => void;
  label: React.ReactNode;
}) {
  const [adding, setAdding] = useState('');
  const add = () => {
    const path = adding.trim().replace(/\\/g, '/');
    if (path && !value.includes(path)) onChange([...value, path]);
    setAdding('');
  };
  return (
    <Field label={label} hint={row.help}>
      <div className="grid gap-2">
        {value.map((path) => (
          <div
            key={path}
            className="flex items-center gap-2 rounded-lg border border-line bg-obsidian px-3 py-2"
          >
            <span className="mono flex-1 truncate text-xs">{path}</span>
            <button
              type="button"
              aria-label={`Remove ${path}`}
              className="text-faint hover:text-danger"
              onClick={() => onChange(value.filter((p) => p !== path))}
            >
              <X size={14} />
            </button>
          </div>
        ))}
        {!value.length && <span className="text-xs text-faint">No folders yet.</span>}
        <div className="flex gap-2">
          <input
            className={inputClass}
            placeholder="Paste a folder path, for example D:/projects/my-app"
            value={adding}
            onChange={(e) => setAdding(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && (e.preventDefault(), add())}
          />
          <Button onClick={add} disabled={!adding.trim()}>
            <FolderPlus size={14} /> Add
          </Button>
        </div>
      </div>
    </Field>
  );
}

/** Help text supports `code` spans only. */
function Markdownish({ text }: { text: string }) {
  const parts = text.split(/(`[^`]+`)/g);
  return (
    <>
      {parts.map((p, i) =>
        p.startsWith('`') ? (
          <code key={i} className="mono rounded bg-zinc-3 px-1 text-[11px] text-copper-glow">
            {p.slice(1, -1)}
          </code>
        ) : (
          <span key={i}>{p}</span>
        ),
      )}
    </>
  );
}
