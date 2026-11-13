// Renders the real avatar window with a simulated PANINI connection that walks through a
// task: thinking → delegating to Search → approval → success. Visual test only.
const listeners: ((event: any) => void)[] = [];
const snapshot = { state: 'ready', task: null, results: [], selected_targets: {} };
(window as any).panini = {
  async invoke(method: string, input: any = {}) {
    const personas = [
      { id: 'panini', name: 'PANINI', color: 'copper' },
      { id: 'scout', name: 'Scout', color: 'cyan' },
      { id: 'quill', name: 'Quill', color: 'violet' },
    ];
    if (method === 'voice-personas') return { personas };
    if (method === 'media') {
      ((window as any).voiceOffers ||= []).push(input);
      emit('media.connected', {
        generation: input.generation,
        persona: personas.find((p) => p.id === input.persona_id),
      });
      return { sdp: 'scripted SDP answer' };
    }
    if (method === 'media-close') {
      setTimeout(() => emit('media.closed', { generation: input.generation }), 40);
      return { status: 'closed' };
    }
    if (method === 'status')
      return {
        connected: true,
        preferences: {
          view: new URLSearchParams(location.search).has('personas') ? 'compact' : 'stage',
        },
        snapshot,
      };
    if (method === 'capabilities')
      return {
        capabilities: ['search', 'browser', 'desktop', 'code', 'agent'].map((c) => ({
          capability: c,
          state: 'ready',
          reason: '',
        })),
      };
    if (method === 'targets') return { targets: [] };
    return {};
  },
  subscribe(callback: (event: any) => void) {
    listeners.push(callback);
    return () => {};
  },
  heartbeat() {},
};
const emit = (type: string, data: any, capability?: string) =>
  listeners.forEach((l) => l({ type, data, capability, task_id: 't1' }));
(window as any).emitPersonaPreview = (persona: unknown, text: string) =>
  emit('task.running', { persona, message: text, agent_event: 'persona.message' }, 'agent');
const script: [number, () => void][] = [
  [2500, () => emit('task.preparing', { message: 'Preparing your instruction.' }, 'search')],
  [
    2500,
    () =>
      emit(
        'task.running',
        {
          message: 'Retrieving and ranking web results.',
          steps: [{ capability: 'search', instruction: 'Current Node.js LTS', state: 'running' }],
        },
        'search',
      ),
  ],
  [
    3000,
    () =>
      emit(
        'task.awaiting_confirmation',
        { message: 'Confirm the proposed action in Chrome.' },
        'browser',
      ),
  ],
  [
    3000,
    () =>
      emit(
        'task.completed',
        {
          message: 'Verified. Node.js 24.21.0 LTS (nodejs.org).',
          outcome: 'verified_complete',
          steps: [{ capability: 'search', instruction: 'Current Node.js LTS', state: 'completed' }],
        },
        'search',
      ),
  ],
];
let delay = 0;
for (const [wait, step] of new URLSearchParams(location.search).has('personas') ? [] : script) {
  delay += wait;
  setTimeout(step, delay);
}
await import('../../src/renderer/main.tsx');
