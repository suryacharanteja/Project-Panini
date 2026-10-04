import type { Draft } from './autosave';
export type Space = { id: string; name: string };
export type PageSummary = {
  id: string;
  space_id: string;
  parent_id: string | null;
  title: string;
  revision: number;
};
export type Document = PageSummary & Draft;
export type PageRevision = Draft & {
  parent_id: string | null;
  deleted_at: number | null;
  created: number;
};
