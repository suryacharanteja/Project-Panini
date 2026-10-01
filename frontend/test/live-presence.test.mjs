import { test } from 'node:test';
import assert from 'node:assert/strict';
import { initialPresence, updatePresence, presenceActivity } from '../public/live-presence.mjs';
import { LiveAudioMeter } from '../public/live-audio.mjs';

const connected = () =>
  updatePresence(initialPresence(), { type: 'connection', state: 'listening' });
const apply = (state, ...events) => events.reduce(updatePresence, state);

test('listening, muting and assistant playback remain independent of browser execution', () => {
  let state = apply(connected(), { type: 'task', state: 'working', message: 'Searching LinkedIn' });
  assert.equal(presenceActivity(state)[1], 'Listening while I work');
  state = updatePresence(state, { type: 'muted', muted: true });
  assert.equal(presenceActivity(state)[0], 'muted');
  assert.equal(state.task, 'working');
  state = updatePresence(state, { type: 'audio', speaking: true });
  assert.equal(presenceActivity(state)[0], 'speaking');
  assert.equal(state.taskDetail, 'Searching LinkedIn');
  state = updatePresence(state, { type: 'audio', speaking: false });
  assert.equal(presenceActivity(state)[0], 'muted');
});

test('transcripts and result cards never fabricate playback or successful completion', () => {
  const state = apply(
    connected(),
    { type: 'task', state: 'working' },
    { type: 'transcript', role: 'assistant', delta: 'I found some results.' },
    { type: 'results', items: [{ title: 'One observed role' }] },
  );
  assert.equal(state.task, 'working');
  assert.equal(state.speaking, false);
  const reporting = updatePresence(state, { type: 'browser', event: { event: 'complete' } });
  assert.equal(reporting.task, 'reporting');
  const completed = updatePresence(reporting, {
    type: 'message',
    feedback: true,
    outcome: 'complete',
    message: 'Search verified.',
  });
  assert.equal(completed.task, 'complete');
});

test('clarification is preserved even after a completed browser action, then clears on resume', () => {
  let state = apply(
    connected(),
    { type: 'task', state: 'needs_information', message: 'Choose a location.' },
    { type: 'message', feedback: true, outcome: 'complete', message: 'Dubai or Abu Dhabi?' },
  );
  assert.equal(state.task, 'needs_information');
  assert.equal(presenceActivity(state)[0], 'question');
  state = updatePresence(state, { type: 'task', state: 'working', message: 'Searching Dubai' });
  assert.equal(state.feedback, '');
  assert.equal(state.task, 'working');
});

test('failure, reconnect and end never appear as successful completion', () => {
  let state = apply(
    connected(),
    { type: 'task', state: 'working' },
    { type: 'message', feedback: true, outcome: 'error', message: 'Page unavailable' },
  );
  assert.equal(state.task, 'blocked');
  state = apply(
    state,
    { type: 'task', state: 'working' },
    { type: 'audio', speaking: true },
    { type: 'connection', state: 'reconnecting', message: 'Connection lost' },
  );
  assert.equal(presenceActivity(state)[0], 'reconnecting');
  state = updatePresence(state, { type: 'connection', state: 'ending' });
  assert.equal(state.task, 'stopped');
  assert.equal(state.speaking, false);
  state = updatePresence(state, { type: 'connection', state: 'idle' });
  assert.equal(presenceActivity(state)[0], 'idle');
});

test('blocked autoplay never shows speaking, and a new conversation clears stale results', () => {
  let state = apply(
    connected(),
    { type: 'playback_blocked', message: 'Play voice' },
    { type: 'audio', speaking: true },
  );
  assert.equal(presenceActivity(state)[0], 'playback');
  assert.equal(state.speaking, false);
  state = apply(state, { type: 'playback_ready' }, { type: 'audio', speaking: true });
  assert.equal(presenceActivity(state)[0], 'speaking');
  state = apply(
    state,
    { type: 'results', items: [{ title: 'Previous' }] },
    { type: 'connection', state: 'connecting' },
  );
  assert.deepEqual(state.results, []);
  assert.equal(state.muted, false);
});

function audioFixture() {
  const contexts = [],
    samples = [],
    frames = new Map();
  let nextFrame = 0;
  class Context {
    state = 'running';
    disconnected = 0;
    constructor() {
      contexts.push(this);
    }
    async resume() {}
    async close() {
      this.state = 'closed';
    }
    createAnalyser() {
      return {
        fftSize: 512,
        frequencyBinCount: 256,
        getFloatTimeDomainData(array) {
          array.fill(this.stream.level);
        },
        getByteFrequencyData(array) {
          array.fill(160);
        },
        disconnect: () => this.disconnected++,
      };
    }
    createMediaStreamSource(stream) {
      return {
        connect: (analyser) => {
          analyser.stream = stream;
        },
        disconnect: () => this.disconnected++,
      };
    }
  }
  const audio = { paused: false, muted: false, volume: 1 };
  const meter = new LiveAudioMeter({
    audio,
    Context,
    onSample: (sample) => samples.push(sample),
    requestFrame: (callback) => {
      frames.set(++nextFrame, callback);
      return nextFrame;
    },
    cancelFrame: (id) => frames.delete(id),
  });
  const tick = (time) => {
    const pending = [...frames.values()];
    frames.clear();
    pending.forEach((callback) => callback(time));
    return samples.at(-1);
  };
  return { meter, audio, contexts, frames, samples, tick };
}

test('meter follows real signal, silence, mute and output audibility; captions are irrelevant', () => {
  const f = audioFixture();
  const input = { level: 0.035 },
    output = { level: 0 };
  f.meter.prepare();
  f.meter.attach('user', input);
  f.meter.attach('assistant', output);
  f.meter.configure({ enabled: true });
  assert.ok(f.tick(0).energy > 0.2);
  f.meter.configure({ muted: true });
  assert.equal(f.tick(40).energy, 0);
  output.level = 0.04;
  assert.equal(f.tick(80).speaking, true);
  assert.ok(f.samples.at(-1).energy > 0.3);
  output.level = 0;
  assert.equal(f.tick(300).speaking, false);
  output.level = 0.04;
  f.audio.paused = true;
  assert.equal(f.tick(340).speaking, false);
  f.audio.paused = false;
  f.audio.volume = 0;
  assert.equal(f.tick(380).speaking, false);
  f.meter.stop();
});

test('hidden or disconnected panels stop sampling, resume cleanly, and never stop session tracks', async () => {
  const f = audioFixture();
  const stream = {
    level: 0.03,
    getTracks() {
      throw new Error('Meter must not own session tracks');
    },
  };
  f.meter.prepare();
  f.meter.prepare();
  assert.equal(f.contexts.length, 1);
  f.meter.attach('user', stream);
  f.meter.configure({ enabled: true });
  assert.equal(f.frames.size, 1);
  f.meter.configure({ visible: false });
  assert.equal(f.frames.size, 0);
  assert.equal(f.samples.at(-1).energy, 0);
  f.meter.configure({ visible: true });
  assert.ok(f.tick(50).energy > 0);
  f.meter.configure({ enabled: false });
  assert.equal(f.frames.size, 0);
  f.meter.stop();
  f.meter.stop();
  assert.equal(f.contexts[0].state, 'closed');
  assert.equal(f.contexts[0].disconnected, 2);
  assert.equal(Object.keys(f.meter.sources).length, 0);
  f.meter.prepare();
  assert.equal(f.contexts.length, 2);
  f.meter.stop();
});

test('replacing a remote track disconnects only its old analyser; blocked Web Audio is harmless', () => {
  const f = audioFixture();
  f.meter.prepare();
  f.meter.attach('assistant', { level: 0.02 });
  f.meter.attach('assistant', { level: 0.07 });
  assert.equal(f.contexts[0].disconnected, 2);
  f.meter.configure({ enabled: true });
  assert.ok(f.tick(0).energy > 0.6);
  f.meter.stop();
  const unavailable = new LiveAudioMeter({
    Context: class {
      constructor() {
        throw new Error('Unavailable');
      }
    },
    onSample() {},
    audio: {},
  });
  assert.doesNotThrow(() => {
    unavailable.prepare();
    unavailable.attach('user', {});
    unavailable.stop();
  });
});
