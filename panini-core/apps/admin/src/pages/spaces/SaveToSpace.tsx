import { useState } from 'react';
import { api, harness } from '../../api';
import { Button, inputClass } from '../../components/ui';

/** The owner selects a destination; the server retrieves and validates the source. */
export function SaveToSpace({
  kind,
  sourceId,
}: {
  kind: 'receipt' | 'result' | 'history' | 'task' | 'conversation' | 'skill';
  sourceId: string;
}) {
  const [spaces, setSpaces] = useState<{ id: string; name: string }[] | null>(null);
  const [destination, setDestination] = useState('');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [saved, setSaved] = useState(false);
  async function show() {
    try {
      const result = await api<{ spaces: { id: string; name: string }[] }>(harness('/spaces'));
      setSpaces(result.spaces);
      setMessage(result.spaces.length ? '' : 'Create a space in Spaces first.');
    } catch (error) {
      setMessage((error as Error).message);
    }
  }
  async function save() {
    setBusy(true);
    try {
      const page = await api<{ deleted_at?: number | null }>(harness('/spaces/save-source'), {
        body: { space_id: destination, kind, source_id: sourceId },
      });
      setSaved(true);
      setSpaces(null);
      setMessage(
        page.deleted_at
          ? 'This source was already saved in Trash. Restore it in Spaces.'
          : 'Saved to your notebook.',
      );
    } catch (error) {
      setMessage((error as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Button disabled={saved} onClick={() => void show()}>
        Save to Space
      </Button>
      {spaces && (
        <>
          <select
            aria-label="Save destination"
            className={inputClass}
            value={destination}
            onChange={(event) => setDestination(event.target.value)}
          >
            <option value="">Choose a space</option>
            {spaces.map((space) => (
              <option key={space.id} value={space.id}>
                {space.name}
              </option>
            ))}
          </select>
          <Button disabled={!destination || busy} onClick={() => void save()}>
            Save {kind}
          </Button>
          <Button disabled={busy} onClick={() => setSpaces(null)}>
            Cancel
          </Button>
        </>
      )}
      {message && (
        <span role="status" className="text-sm text-muted">
          {message}
        </span>
      )}
    </div>
  );
}
