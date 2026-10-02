/**
 * Only strings reach the DOM. Event payloads come from the backend, a model or another app, and rendering an
 * object as a React child throws (the shell crash recorded in project memory), so every card normalizes its
 * props through these helpers first.
 */

export function toText(value: unknown, limit = 2000): string {
  if (value === null || value === undefined) return '';
  if (typeof value === 'string') return value.slice(0, limit);
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  try {
    return JSON.stringify(value).slice(0, limit);
  } catch {
    return '';
  }
}

export type ToolState = 'working' | 'finished' | 'attention' | 'interrupted';

export const STATE_LABEL: Record<ToolState, string> = {
  working: 'Working',
  finished: 'Finished',
  attention: 'Needs attention',
  interrupted: 'Interrupted',
};

const LABELS: Record<string, string> = {
  run_command: 'Running a command',
  read_file: 'Reading a file',
  list_files: 'Listing files',
  open: 'Opening',
  browser: 'Using the browser',
  use_capability: 'Using a capability',
  search_run: 'Searching the web',
  browser_run: 'Working in Chrome',
  desktop_run: 'Working in an app',
  code_run: 'Coding',
  recall: 'Remembering',
  remember: 'Saving a memory',
  forget: 'Forgetting a memory',
  update_plan: 'Updating the plan',
  load_skill: 'Loading a skill',
  create_skill: 'Saving a skill',
  schedule_task: 'Scheduling a task',
  watch: 'Setting up a watch',
  use_connector: 'Using a connector',
  ask_agent: 'Asking another agent',
  run_subagents: 'Starting helpers',
  activity_search: 'Searching past activity',
  finish: 'Finishing',
};

/** A human label for a tool name ("run_command" → "Running a command"). */
export function toolLabel(name: unknown): string {
  const text = toText(name, 80);
  if (text.startsWith('client_')) return `In your app: ${text.slice(7).replace(/_/g, ' ')}`;
  return LABELS[text] || text.replace(/_/g, ' ') || 'Tool';
}

/** One line describing a tool call's arguments, from a redacted view (never raw values). */
export function toolDetail(args: unknown): string {
  if (!args || typeof args !== 'object') return toText(args, 200);
  const record = args as Record<string, unknown>;
  for (const key of ['capability', 'instruction', 'command', 'path', 'target', 'query', 'url']) {
    const value = record[key];
    if (value === undefined) continue;
    if (value && typeof value === 'object' && 'preview' in (value as object))
      return toText((value as { preview: unknown }).preview, 200);
    return `${key}: ${toText(value, 200)}`;
  }
  return toText(args, 200);
}

/** The state a tool result implies: an error is "needs attention". */
export function resultState(output: unknown): ToolState {
  if (output && typeof output === 'object' && 'error' in (output as object)) return 'attention';
  if (typeof output === 'string' && /"error"\s*:/.test(output)) return 'attention';
  return 'finished';
}
