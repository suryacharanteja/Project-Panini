import { LivePanel } from '/live-panel.mjs';
import { WakePhrase } from '/live-wake.mjs';

const el = (id) => document.getElementById(id);
const panel = new LivePanel(el('live-panel'));
let context, source;
let time = 0;
let recognition;
class FakeRecognition {
  constructor() {
    recognition = this;
  }
  start() {
    this.onstart?.();
  }
  abort() {
    queueMicrotask(() => this.onend?.());
  }
  result(text, final = true, alternatives = []) {
    const result = [{ transcript: text }];
    result.push(...alternatives.map((transcript) => ({ transcript })));
    result.isFinal = final;
    this.onresult?.({ resultIndex: 0, results: [result] });
  }
}
const wake = new WakePhrase({
  Recognition: FakeRecognition,
  onState: (state) => panel.wakeStatus(state),
  showDetails: () => el('live-wake-details').open,
  onDetail: (message) => panel.wakeDetail(message),
  onWake: () => begin(),
  getBlocker: () =>
    document.hidden
      ? 'Wake listening paused · Return to this page to resume.'
      : panel.state.connection !== 'idle'
        ? 'Wake phrase paused during your live conversation.'
        : '',
});
el('live-wake-enabled').onchange = (event) => {
  void wake.enable(event.target.checked);
};
el('live-wake-language').onchange = (event) => {
  void wake.setLanguage(event.target.value);
};
document.addEventListener('visibilitychange', () => {
  void wake.sync();
});
function silence() {
  source?.stop();
  source = null;
  context?.close();
  context = null;
}
function signal(role) {
  silence();
  context = new AudioContext();
  const destination = context.createMediaStreamDestination();
  const buffer = context.createBuffer(1, context.sampleRate, context.sampleRate);
  const samples = buffer.getChannelData(0);
  for (let i = 0; i < samples.length; i++)
    samples[i] = Math.sin(i * 0.15) * Math.sin(i * 0.0004) * 0.09;
  source = context.createBufferSource();
  source.buffer = buffer;
  source.loop = true;
  source.connect(destination);
  source.start();
  // Synthetic stream is analysed but never played through this machine's speakers.
  panel.meter.audio = { paused: false, volume: 1, muted: false };
  panel.meter.attach(role, destination.stream);
}
function begin() {
  silence();
  panel.connection('connecting', 'Component test only');
  panel.connection('listening', 'Connected to the component fixture');
  void wake.sync();
}
function say(role, delta) {
  time += 3000;
  panel.event({ type: 'transcript', role, delta, start_ms: time, end_ms: time + 1000 });
}
const cases = {
  'Simulate Hey Panini': () => recognition?.result('Hey Panini'),
  'Interim wake phrase': () => recognition?.result('Okay, hey Panini', false),
  'Alternate wake phrase': () => recognition?.result('hey banana', true, ['hey pa nini']),
  'Unrelated speech': () => recognition?.result('Please read the page'),
  'Wake permission denied': () => recognition?.onerror?.({ error: 'not-allowed' }),
  Listening: () => {
    begin();
    say('user', 'Find AI manager jobs on LinkedIn.');
    signal('user');
  },
  'Browser working': () => {
    begin();
    panel.event({
      type: 'task',
      state: 'working',
      message: 'Reading the search results for AI manager roles.',
    });
    panel.event({
      type: 'page',
      title: 'LinkedIn · AI manager jobs',
      url: 'https://www.linkedin.com/jobs/search/',
    });
    say('user', 'Focus on Dubai and Abu Dhabi.');
    signal('user');
  },
  Speaking: () => {
    begin();
    say('assistant', 'I found five roles. Would you like to hear the first one?');
    signal('assistant');
  },
  'Needs answer': () => {
    begin();
    panel.event({
      type: 'task',
      state: 'needs_information',
      message: 'A location is needed to continue.',
    });
    panel.event({
      type: 'message',
      feedback: true,
      outcome: 'complete',
      message: 'Which location should I search: Dubai or Abu Dhabi?',
    });
  },
  Results: () => {
    begin();
    panel.event({
      type: 'message',
      feedback: true,
      outcome: 'complete',
      message: 'Two matching roles were read from the test page.',
    });
    panel.event({
      type: 'results',
      items: [
        {
          id: 'one',
          title: 'AI Engineering Manager',
          detail: 'Example company · Dubai · Full-time',
          url: 'https://example.com/jobs/one',
        },
        {
          id: 'two',
          title: 'Applied AI Lead',
          detail: 'Example company · Abu Dhabi · Hybrid',
          url: 'https://example.com/jobs/two',
        },
      ],
    });
    say('assistant', 'The roles are ready. Which one would you like to review?');
  },
  Failure: () => {
    begin();
    panel.event({
      type: 'message',
      feedback: true,
      outcome: 'error',
      message: 'The page could not be reached. No result has been verified.',
    });
  },
  Reconnect: () => {
    silence();
    panel.connection('reconnecting', 'Audio interrupted. Trying to reconnect…');
  },
  Silence: silence,
  'Unsafe result': () => {
    begin();
    panel.event({
      type: 'results',
      items: [
        {
          id: 'unsafe',
          title: '<img onerror=alert(1)>',
          detail: '<script>untrusted content</script>',
          url: 'javascript:alert(1)',
        },
      ],
    });
  },
};
const toolbar = document.createElement('section');
toolbar.setAttribute('aria-label', 'Component test scenarios');
toolbar.className = 'command-buttons';
toolbar.style.marginBottom = '20px';
const label = document.createElement('p');
label.textContent =
  'Component test · Synthetic audio and example events · No microphone or provider connection';
label.style.flexBasis = '100%';
toolbar.append(label);
for (const [name, action] of Object.entries(cases)) {
  const button = document.createElement('button');
  button.textContent = name;
  button.className = 'secondary';
  button.onclick = action;
  toolbar.append(button);
}
el('live-panel').before(toolbar);
el('live-start').onclick = begin;
el('live-end').onclick = () => {
  silence();
  panel.connection('ending', 'Ending…');
  panel.connection('idle', 'Conversation ended. Microphone off.');
  void wake.sync();
};
el('live-mute').onclick = () => panel.event({ type: 'muted', muted: !panel.state.muted });
el('live-stop').onclick = () => panel.event({ type: 'task_stopped' });
window.addEventListener('pagehide', () => {
  wake.dispose();
  silence();
  panel.dispose();
});
