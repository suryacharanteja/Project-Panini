import { Card } from '../../components/ui';
import type { PageSummary } from './types';

export function SpaceLibrary({
  pages,
  spaceId,
  trash,
  grid,
  onOpen,
  onLifecycle,
  onDestroy,
}: {
  pages: PageSummary[];
  spaceId: string;
  trash: boolean;
  grid: boolean;
  onOpen: (id: string) => Promise<void>;
  onLifecycle: (id: string, restore: boolean) => Promise<void>;
  onDestroy: (id: string) => Promise<void>;
}) {
  function depth(doc: PageSummary) {
    let count = 0,
      parent = doc.parent_id;
    const seen = new Set<string>();
    while (parent && !seen.has(parent) && count < 12) {
      seen.add(parent);
      count++;
      parent = pages.find((p) => p.id === parent)?.parent_id ?? null;
    }
    return count;
  }
  return (
    <Card>
      <nav aria-label="Pages" className={grid ? 'grid grid-cols-2 gap-3' : 'grid gap-2'}>
        {pages.map((p) => (
          <div
            key={p.id}
            className="flex flex-wrap items-center gap-1 min-w-0"
            style={{ paddingLeft: depth(p) * 12 }}
          >
            <button
              className="min-w-0 break-words text-left text-sm p-2 rounded hover:bg-zinc-3"
              onClick={() => !trash && void onOpen(p.id)}
              disabled={trash}
            >
              {p.title}
            </button>
            <button
              className="ml-auto text-xs text-muted"
              aria-label={`${trash ? 'Restore' : 'Trash'} ${p.title}`}
              onClick={() => void onLifecycle(p.id, trash)}
            >
              {trash ? 'Restore' : 'Trash'}
            </button>
            {trash && (
              <button
                className="text-xs text-red-400"
                aria-label={`Permanently delete ${p.title}`}
                onClick={() => void onDestroy(p.id)}
              >
                Delete forever
              </button>
            )}
          </div>
        ))}
        {spaceId && !pages.length && <p className="text-muted text-sm">No pages yet.</p>}
      </nav>
    </Card>
  );
}
