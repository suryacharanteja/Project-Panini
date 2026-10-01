/** Presentation state only. Browser outcomes come from the existing authoritative event stream. */
export function initialPresence() {
  return {
    connection: 'idle',
    muted: false,
    speaking: false,
    routing: false,
    playbackBlocked: false,
    task: 'ready',
    taskDetail: 'Try: “Hey PANINI, find five AI manager jobs on LinkedIn.”',
    page: '',
    feedback: '',
    results: [],
    notice: 'Choose your browser below, then start a conversation.',
    error: false,
  };
}

export function updatePresence(previous, event) {
  let state = { ...previous };
  if (event.type === 'connection') {
    if (event.state === 'connecting') state = initialPresence();
    state.connection = event.state;
    state.notice = event.message || '';
    state.error = event.state === 'error';
    if (['idle', 'ending', 'error'].includes(event.state)) {
      state.muted = state.speaking = state.routing = state.playbackBlocked = false;
      if (['working', 'reporting', 'needs_information'].includes(state.task)) {
        state.task = 'stopped';
        state.taskDetail = 'Browser work stopped with the conversation.';
      }
    }
  }
  if (event.type === 'muted') state.muted = event.muted;
  if (event.type === 'playback_blocked') {
    state.playbackBlocked = true;
    state.speaking = false;
    state.notice = event.message;
  }
  if (event.type === 'playback_ready') {
    state.playbackBlocked = false;
    state.notice = '';
  }
  if (event.type === 'audio')
    state.speaking = event.speaking && state.connection === 'listening' && !state.playbackBlocked;
  if (event.type === 'routing') {
    state.routing = event.stage === 'received';
    if (state.routing && !['working', 'reporting'].includes(state.task)) {
      state.task = 'understanding';
      state.taskDetail = 'Understanding your request…';
    }
    if (['interpreted', 'acknowledged'].includes(event.stage) && event.intent === 'reply') {
      if (state.task === 'understanding') state.task = 'ready';
      if (event.message) state.taskDetail = event.message;
    }
  }
  if (event.type === 'page') {
    let hostname = '';
    try {
      hostname = new URL(event.url).hostname;
    } catch {}
    state.page = String(event.title || hostname).slice(0, 160);
  }
  if (event.type === 'task') {
    state.routing = false;
    state.task = event.error_code ? 'error' : event.state === 'listening' ? 'ready' : event.state;
    state.taskDetail = event.message || '';
    if (state.task === 'working') {
      state.feedback = '';
      state.error = false;
      state.notice = '';
    }
  }
  if (event.type === 'browser') {
    const browser = event.event || {};
    if (browser.event === 'complete') state.task = 'reporting';
    else if (['error', 'timeout', 'incomplete'].includes(browser.event)) state.task = 'blocked';
    else if (browser.event === 'cancelled') state.task = 'stopped';
    if (browser.message || browser.error) state.taskDetail = browser.message || browser.error;
  }
  if (event.type === 'message' && event.feedback) {
    state.feedback = event.message || '';
    if (state.task !== 'needs_information') state.taskDetail = state.feedback;
    if (state.task !== 'needs_information') {
      state.task =
        event.outcome === 'complete'
          ? 'complete'
          : event.outcome === 'cancelled'
            ? 'stopped'
            : 'blocked';
    }
    state.routing = false;
  }
  if (event.type === 'results') state.results = Array.isArray(event.items) ? event.items : [];
  if (event.type === 'error') {
    state.error = true;
    state.notice = event.message || 'Something went wrong. Please try again.';
  }
  if (event.type === 'task_stopped') {
    state.task = 'stopped';
    state.taskDetail = 'Browser task stopped.';
    state.routing = false;
  }
  return state;
}

export function presenceActivity(state) {
  if (state.connection === 'idle') return ['idle', 'Ready when you are'];
  if (state.connection === 'error') return ['error', 'Unable to connect'];
  if (state.connection === 'connecting') return ['connecting', 'Connecting your microphone'];
  if (state.connection === 'ending') return ['ending', 'Ending conversation'];
  if (state.connection === 'reconnecting')
    return ['reconnecting', 'Reconnecting · Audio interrupted'];
  if (state.playbackBlocked) return ['playback', 'Press Play voice to hear PANINI'];
  if (state.speaking) return ['speaking', 'PANINI is speaking'];
  if (state.muted) return ['muted', 'Microphone muted'];
  if (state.task === 'needs_information') return ['question', 'Waiting for your answer'];
  if (state.routing) return ['thinking', 'Understanding your request'];
  return [
    'listening',
    ['working', 'reporting'].includes(state.task) ? 'Listening while I work' : 'Listening to you',
  ];
}

export const taskLabels = {
  ready: 'Browser ready',
  understanding: 'Understanding your request',
  working: 'Browser working',
  reporting: 'Preparing browser feedback',
  needs_information: 'Your answer is needed',
  complete: 'Task complete',
  blocked: 'Task needs attention',
  error: 'Task encountered an error',
  stopped: 'Task stopped',
};
