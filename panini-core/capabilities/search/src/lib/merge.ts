import { canonicalUrl, type RankedItem } from './rank';

/**
 * Results now arrive one engine at a time. The same URL from a second
 * engine is folded into the row we already have: engines are unioned (the
 * agreement bonus in ranking comes from that), the higher relevance wins,
 * and the better rank is kept. Used by the client as lanes stream in and by
 * runSearch for tests.
 */
export function mergeItems(existing: RankedItem[], incoming: RankedItem[]): RankedItem[] {
  const byUrl = new Map<string, RankedItem>();
  const out: RankedItem[] = [];
  for (const item of existing) {
    byUrl.set(canonicalUrl(item.url), item);
    out.push(item);
  }
  for (const item of incoming) {
    const key = canonicalUrl(item.url);
    const found = byUrl.get(key);
    if (!found) {
      byUrl.set(key, item);
      out.push(item);
      continue;
    }
    // Keep the date, age and score from the same source. A structured API
    // date wins over a snippet estimate regardless of lane arrival order.
    const publication =
      (!found.publishedDate && item.publishedDate) || found.ageHours === null ? item : found;
    const merged: RankedItem = {
      ...found,
      engines: [...new Set([...found.engines, ...item.engines])],
      relevance: Math.max(found.relevance, item.relevance),
      ranked: found.ranked || item.ranked,
      position: Math.min(found.position, item.position),
      publishedDate: publication.publishedDate,
      ageHours: publication.ageHours,
      freshness: publication.freshness,
      snippet: found.snippet.length >= item.snippet.length ? found.snippet : item.snippet,
    };
    byUrl.set(key, merged);
    out[out.indexOf(found)] = merged;
  }
  return out;
}
