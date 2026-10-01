import { isSourceId, isWindowId, type SourceId, type WindowId } from './sources';

export interface AskRequest {
  q: string;
  w?: WindowId;
  s?: SourceId[];
}

export function validateAskRequest(input: unknown): AskRequest {
  if (typeof input !== 'object' || input === null) {
    throw new Error('Invalid input');
  }
  const { q, w, s } = input as Record<string, unknown>;
  if (typeof q !== 'string' || q.trim().length === 0 || q.length > 300) {
    throw new Error('q must be a non-empty string up to 300 characters');
  }
  const out: AskRequest = { q: q.trim() };
  if (typeof w === 'string' && isWindowId(w)) out.w = w;
  if (Array.isArray(s)) {
    const ids = [
      ...new Set(s.filter((v): v is SourceId => typeof v === 'string' && isSourceId(v))),
    ];
    if (ids.length > 0) out.s = ids;
  }
  return out;
}
