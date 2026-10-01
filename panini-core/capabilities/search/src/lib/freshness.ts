/**
 * Prefer a structured publication date. Snippet parsing remains a
 * fallback for engines or responses without the structured field.
 */

const RELATIVE_RE = /^\s*(\d+)\s+(minute|min|hour|hr|day|week|month|year)s?\s+ago\b/i;
const ABSOLUTE_RE =
  /^\s*((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4})\b/i;
/** Vertical engines (arXiv) put an ISO date somewhere in a " | "-joined snippet. */
const ISO_RE = /(?:^|\|\s*)(\d{4}-\d{2}-\d{2})(?:\s*\||\s|$)/;

const UNIT_HOURS: Record<string, number> = {
  minute: 1 / 60,
  min: 1 / 60,
  hour: 1,
  hr: 1,
  day: 24,
  week: 24 * 7,
  month: 24 * 30,
  year: 24 * 365,
};

export interface Publication {
  /** Original normalized date, preserving day versus second precision. */
  publishedDate?: string;
  /** Day-only dates use UTC midnight as a sorting/scoring estimate. */
  ageHours: number | null;
}

/** Accept only the two public shapes, including a valid calendar date. */
export function resolvePublication(
  publishedDate: unknown,
  snippet: string,
  now = Date.now(),
): Publication {
  if (
    typeof publishedDate === 'string' &&
    /^\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}:\d{2}Z)?$/.test(publishedDate)
  ) {
    const ts = Date.parse(publishedDate);
    if (Number.isFinite(ts)) {
      const canonical = new Date(ts).toISOString().replace('.000Z', 'Z');
      if (canonical.slice(0, publishedDate.length) === publishedDate) {
        return { publishedDate, ageHours: Math.max(0, (now - ts) / 3_600_000) };
      }
    }
  }
  return { ageHours: parseAgeHours(snippet, now) };
}

/** A day-only date may refer to any moment in that UTC day. */
export function isPublicationStale(publication: Publication, maxAgeHours: number): boolean {
  if (publication.ageHours === null) return false;
  const uncertainty = publication.publishedDate?.length === 10 ? 24 : 0;
  return publication.ageHours - uncertainty > maxAgeHours;
}

export function formatPublicationAge({ publishedDate, ageHours }: Publication): string | null {
  // Show the calendar date instead of inventing an hour of publication.
  if (publishedDate?.length === 10) return publishedDate;
  if (ageHours === null) return null;
  if (ageHours < 1) return 'just now';
  if (ageHours < 48) return `${Math.round(ageHours)}h ago`;
  if (ageHours < 24 * 14) return `${Math.round(ageHours / 24)}d ago`;
  return `${Math.round(ageHours / (24 * 7))}w ago`;
}

/** Age in hours parsed from the snippet prefix, or null when absent. */
export function parseAgeHours(snippet: string, now = Date.now()): number | null {
  const rel = RELATIVE_RE.exec(snippet);
  if (rel) {
    const n = Number(rel[1]);
    const unit = rel[2]!.toLowerCase();
    const factor = UNIT_HOURS[unit];
    if (factor !== undefined) return n * factor;
  }
  const abs = ABSOLUTE_RE.exec(snippet);
  if (abs) {
    const ts = Date.parse(`${abs[1]!} UTC`);
    if (!Number.isNaN(ts)) return Math.max(0, (now - ts) / 3_600_000);
  }
  const iso = ISO_RE.exec(snippet);
  if (iso) {
    const ts = Date.parse(`${iso[1]!}T00:00:00Z`);
    if (!Number.isNaN(ts)) return Math.max(0, (now - ts) / 3_600_000);
  }
  return null;
}

/** Remove the age prefix so the snippet reads as prose. */
export function stripAgePrefix(snippet: string): string {
  return snippet
    .replace(RELATIVE_RE, '')
    .replace(ABSOLUTE_RE, '')
    .replace(/^\s*(\.\.\.|…|·|-)\s*/, '')
    .trim();
}

/**
 * 1.0 for brand new, decaying linearly to 0 at the edge of the window.
 * Unknown age gets a flat prior so it neither wins nor sinks on freshness.
 */
export function freshnessScore(ageHours: number | null, windowHours: number): number {
  // No window: freshness is not a criterion, so every item gets the same value.
  if (!Number.isFinite(windowHours)) return 0.5;
  if (ageHours === null) return 0.35;
  return Math.max(0, Math.min(1, 1 - ageHours / windowHours));
}
