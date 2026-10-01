import { useEffect, useMemo, useRef, useState } from 'react';
import type { RankedItem, SortMode } from './rank';
import { EMPTY_PLACEMENT, place, type Placement } from './stable-order';

/** React wrapper around `place`: see src/lib/stable-order.ts for the rules. */
export function useStableOrder(items: RankedItem[], mode: SortMode): RankedItem[] {
  const [placement, setPlacement] = useState<Placement>(EMPTY_PLACEMENT);
  const lastMode = useRef(mode);
  const byId = useMemo(() => new Map(items.map((i) => [i.id, i])), [items]);

  useEffect(() => {
    const modeChanged = lastMode.current !== mode;
    lastMode.current = mode;
    setPlacement((prev) => place(prev, items, mode, modeChanged));
  }, [items, mode]);

  return useMemo(
    () => placement.order.map((id) => byId.get(id)).filter((i): i is RankedItem => Boolean(i)),
    [placement, byId],
  );
}
