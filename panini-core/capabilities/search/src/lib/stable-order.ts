import { compareItems, type RankedItem, type SortMode } from './rank';

export interface Placement {
  /** Ids in display order: score-placed rows first, then unranked rows in arrival order. */
  order: string[];
  /** Ids that have been placed by score. A row that was on screen unranked is not in here yet. */
  placed: Set<string>;
}

export const EMPTY_PLACEMENT: Placement = { order: [], placed: new Set() };

/**
 * Next display order for a list that grows while the reader is looking at it.
 *
 * Rows already placed by score keep their order. A row that is newly ranked
 * (including one that was on screen unranked a moment ago) is inserted where
 * its score puts it. Unranked rows wait at the bottom. A sort-mode change
 * re-sorts everything.
 */
export function place(
  prev: Placement,
  items: RankedItem[],
  mode: SortMode,
  modeChanged: boolean,
): Placement {
  if (items.length === 0) return EMPTY_PLACEMENT;
  const byId = new Map(items.map((i) => [i.id, i]));

  if (modeChanged) {
    const sorted = [...items].sort((a, b) => compareItems(a, b, mode));
    return {
      order: sorted.map((i) => i.id),
      placed: new Set(sorted.filter((i) => i.ranked).map((i) => i.id)),
    };
  }

  const placed = new Set(prev.placed);
  const ranked = prev.order.filter((id) => placed.has(id) && byId.get(id)?.ranked);
  const fresh = items
    .filter((i) => i.ranked && !placed.has(i.id))
    .sort((a, b) => compareItems(a, b, mode));
  for (const item of fresh) {
    let at = ranked.length;
    for (let i = 0; i < ranked.length; i += 1) {
      const other = byId.get(ranked[i]!);
      if (other && compareItems(item, other, mode) < 0) {
        at = i;
        break;
      }
    }
    ranked.splice(at, 0, item.id);
    placed.add(item.id);
  }
  const unranked = items.filter((i) => !i.ranked).map((i) => i.id);
  return { order: [...ranked, ...unranked], placed };
}
