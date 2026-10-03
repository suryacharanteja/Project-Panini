import { useState } from 'react';
import { admin, api, useResource } from '../api';
import { Button, Card, Empty, Field, inputClass, Page, useAction } from '../components/ui';

type Persona = {
  id: string;
  name: string;
  role: string;
  instructions: string;
  color: string;
  memory_scope: string;
  memory_write: boolean;
  permissions: string[];
  voice?: string | null;
  computer_id?: string | null;
  model_tier?: string | null;
};
type Catalog = {
  personas: Persona[];
  permission_options: string[];
  colors: string[];
  enabled: boolean;
  surface_defaults?: Record<string, string>;
};

export function PersonasPage() {
  const resource = useResource<Catalog>(admin('/personas'));
  const [editing, setEditing] = useState<Persona | null>(null);
  const [newId, setNewId] = useState('');
  const [isNew, setIsNew] = useState(false);
  const [preview, setPreview] = useState<{ id: string; text: string } | null>(null);
  const { busy, run } = useAction();
  const edit = (patch: Partial<Persona>) => setEditing((p) => (p ? { ...p, ...patch } : p));
  const save = () =>
    run(
      'save',
      async () => {
        if (!editing) return;
        const id = isNew ? newId : editing.id;
        const { name, role, instructions, color, memory_scope, memory_write } = editing;
        const values = {
          name,
          role,
          instructions,
          color,
          memory_scope,
          memory_write: Boolean(memory_write),
          voice: editing.voice || null,
          model_tier: editing.model_tier || null,
          ...(isNew && !editing.computer_id ? {} : { computer_id: editing.computer_id || null }),
        };
        if (isNew) await api(admin('/personas'), { body: { id, values } });
        else await api(admin(`/personas/${id}`), { method: 'PATCH', body: values });
        if (id !== 'panini')
          await api(admin(`/personas/${id}/permissions`), {
            method: 'PUT',
            body: { permissions: editing.permissions },
          });
        setEditing(null);
        await resource.reload();
      },
      'Persona saved.',
    );

  return (
    <Page
      title="Personas"
      eyebrow="Your specialist team"
      actions={
        <Button
          disabled={Boolean(busy)}
          onClick={() => {
            setNewId('');
            setIsNew(true);
            setEditing({
              id: '',
              name: '',
              role: 'Specialist',
              instructions: 'Help with the assigned task.',
              color: 'copper',
              memory_scope: 'persona',
              memory_write: false,
              permissions: ['research', 'spaces_read'],
            });
          }}
        >
          New persona
        </Button>
      }
    >
      <p className="mb-5 text-sm text-muted">
        {resource.data?.enabled
          ? 'Address a specialist with @Name. Each uses its own permissions and memory scope.'
          : 'Personas are in development. Enable PANINI_PERSONAS after qualification to use specialists in conversations.'}
      </p>
      {resource.error && (
        <p role="alert" className="mb-4 text-danger">
          {resource.error}
        </p>
      )}
      {resource.data && (
        <Card title="Default identity by surface" className="mb-5">
          <div className="grid gap-3 sm:grid-cols-3">
            {['avatar', 'phone_app', 'agui'].map((surface) => (
              <Field key={surface} label={surface.replaceAll('_', ' ')}>
                <select
                  className={inputClass}
                  disabled={Boolean(busy)}
                  value={resource.data?.surface_defaults?.[surface] || 'panini'}
                  onChange={(event) =>
                    run(`surface:${surface}`, async () => {
                      await api(admin('/personas/surfaces/default'), {
                        method: 'PUT',
                        body: { surface, persona_id: event.target.value },
                      });
                      await resource.reload();
                    })
                  }
                >
                  {resource.data?.personas.map((persona) => (
                    <option key={persona.id} value={persona.id}>
                      {persona.name}
                    </option>
                  ))}
                </select>
              </Field>
            ))}
          </div>
        </Card>
      )}
      <div className="grid items-start gap-5 lg:grid-cols-2">
        <Card title="Team">
          {resource.loading ? (
            <Empty>Loading personas…</Empty>
          ) : (
            resource.data?.personas.map((p) => (
              <div key={p.id} className="mb-3 rounded-lg border border-line p-4">
                <h2 className="text-slate">
                  {p.name} <span className="text-xs text-muted">@{p.id}</span>
                </h2>
                <p className="my-2 text-sm text-muted">{p.role}</p>
                <p className="mb-3 text-xs text-faint">
                  {p.id === 'panini'
                    ? 'Owner permissions'
                    : p.permissions.join(' · ') || 'No permissions'}
                </p>
                <Button
                  disabled={Boolean(busy)}
                  onClick={() => {
                    setIsNew(false);
                    setEditing({ ...p, permissions: [...p.permissions] });
                  }}
                >
                  Edit
                </Button>
                <Button
                  disabled={Boolean(busy) || !resource.data?.enabled}
                  onClick={() =>
                    run(`test:${p.id}`, async () => {
                      const result = await api<{ message: string }>(
                        admin(`/personas/${p.id}/test`),
                        { body: { text: 'Introduce yourself and explain your role.' } },
                      );
                      setPreview({ id: p.id, text: result.message });
                    })
                  }
                >
                  Test identity
                </Button>
                {preview?.id === p.id && (
                  <p className="mt-3 whitespace-pre-wrap text-sm text-muted">{preview.text}</p>
                )}
                {p.id !== 'panini' && (
                  <Button
                    tone="danger"
                    disabled={Boolean(busy)}
                    onClick={() =>
                      run(
                        `archive:${p.id}`,
                        async () => {
                          await api(admin(`/personas/${p.id}`), { method: 'DELETE' });
                          if (editing?.id === p.id) setEditing(null);
                          await resource.reload();
                        },
                        'Persona archived and permissions revoked.',
                      )
                    }
                  >
                    Archive
                  </Button>
                )}
              </div>
            ))
          )}
        </Card>
        {editing && (
          <Card title={isNew ? 'Create specialist' : `Edit ${editing.name}`}>
            <form
              className="grid gap-4"
              onSubmit={(e) => {
                e.preventDefault();
                void save();
              }}
            >
              {isNew && (
                <Field label="ID">
                  <input
                    className={inputClass}
                    required
                    maxLength={41}
                    pattern="[a-z][a-z0-9-]*"
                    value={newId}
                    onChange={(e) => setNewId(e.target.value)}
                  />
                </Field>
              )}
              <Field label="Name">
                <input
                  className={inputClass}
                  required
                  maxLength={80}
                  value={editing.name}
                  onChange={(e) => edit({ name: e.target.value })}
                />
              </Field>
              <Field label="Role">
                <input
                  className={inputClass}
                  required
                  maxLength={300}
                  value={editing.role}
                  onChange={(e) => edit({ role: e.target.value })}
                />
              </Field>
              <Field label="Instructions">
                <textarea
                  className={inputClass}
                  required
                  rows={8}
                  maxLength={8000}
                  value={editing.instructions}
                  onChange={(e) => edit({ instructions: e.target.value })}
                />
              </Field>
              <Field label="Colour">
                <select
                  className={inputClass}
                  value={editing.color}
                  onChange={(e) => edit({ color: e.target.value })}
                >
                  {resource.data?.colors.map((c) => (
                    <option key={c}>{c}</option>
                  ))}
                </select>
              </Field>
              <Field label="Voice">
                <input
                  className={inputClass}
                  maxLength={100}
                  value={editing.voice || ''}
                  onChange={(e) => edit({ voice: e.target.value || null })}
                />
              </Field>
              <Field label="Model tier">
                <select
                  className={inputClass}
                  value={editing.model_tier || ''}
                  onChange={(e) => edit({ model_tier: e.target.value || null })}
                >
                  <option value="">Inherit run model</option>
                  {['fast', 'agent', 'deep'].map((t) => (
                    <option key={t}>{t}</option>
                  ))}
                </select>
              </Field>
              <Field label="Sandbox computer ID">
                <input
                  className={inputClass}
                  maxLength={41}
                  value={editing.computer_id || ''}
                  onChange={(e) => edit({ computer_id: e.target.value || null })}
                />
              </Field>
              {editing.id !== 'panini' && (
                <>
                  <Field label="Memory scope">
                    <select
                      className={inputClass}
                      value={editing.memory_scope}
                      onChange={(e) => edit({ memory_scope: e.target.value })}
                    >
                      {['persona', 'global', 'none'].map((c) => (
                        <option key={c}>{c}</option>
                      ))}
                    </select>
                  </Field>
                  <label className="text-sm text-muted">
                    <input
                      type="checkbox"
                      checked={Boolean(editing.memory_write)}
                      onChange={(e) => edit({ memory_write: e.target.checked })}
                    />{' '}
                    Allow memory writes within this scope
                  </label>
                  <fieldset className="grid gap-2">
                    <legend className="mb-2 text-sm text-slate">Permissions</legend>
                    {resource.data?.permission_options.map((permission) => (
                      <label key={permission} className="text-sm text-muted">
                        <input
                          type="checkbox"
                          checked={editing.permissions.includes(permission)}
                          onChange={(e) =>
                            edit({
                              permissions: e.target.checked
                                ? [...editing.permissions, permission]
                                : editing.permissions.filter((p) => p !== permission),
                            })
                          }
                        />{' '}
                        {permission.replaceAll('_', ' ')}
                      </label>
                    ))}
                  </fieldset>
                </>
              )}
              <div className="flex gap-2">
                <Button tone="primary" type="submit" busy={Boolean(busy)}>
                  Save
                </Button>
                <Button type="button" disabled={Boolean(busy)} onClick={() => setEditing(null)}>
                  Cancel
                </Button>
              </div>
            </form>
          </Card>
        )}
      </div>
    </Page>
  );
}
