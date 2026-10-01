import { SOURCE_GROUPS, sourceById } from '@/lib/sources';
import { SourceIcon } from './source-icon';

/** A grouped source catalog keeps the growing search roster easy to scan. */
export function EngineStrip() {
  return (
    <section aria-labelledby="search-locations" className="w-full">
      <h2
        className="text-xs font-semibold uppercase tracking-wider text-muted-foreground"
        id="search-locations"
      >
        Search locations
      </h2>
      <div className="mt-3 grid gap-4 sm:grid-cols-2">
        {SOURCE_GROUPS.map((group) => (
          <section key={group.id} aria-label={group.label}>
            <h3 className="text-xs font-medium text-foreground">{group.label}</h3>
            <ul className="mt-2 flex flex-wrap gap-2">
              {group.sourceIds.map((id) => {
                const source = sourceById(id);
                return (
                  <li key={id} title={source.description}>
                    <span className="chip inline-flex items-center gap-1.5 rounded-full border bg-card px-2.5 py-1 text-xs text-muted-foreground">
                      <SourceIcon className="size-3.5" id={id} on />
                      {source.label}
                    </span>
                  </li>
                );
              })}
            </ul>
          </section>
        ))}
      </div>
    </section>
  );
}
