import { test } from 'node:test';
import assert from 'node:assert/strict';
import { LiveVoice } from '../public/live-voice.mjs';

function fixture({ permission, rejectSession = false } = {}) {
  const calls = [],
    states = [],
    locks = [],
    events = [],
    mediaEvents = [];
  const track = {
    enabled: true,
    stopped: false,
    stop() {
      this.stopped = true;
    },
  };
  const stream = { getTracks: () => [track], getAudioTracks: () => [track] };
  class Peer {
    iceGatheringState = 'complete';
    connectionState = 'new';
    addTrack() {}
    createDataChannel() {
      return { close() {} };
    }
    async createOffer() {
      return { type: 'offer', sdp: 'valid offer' };
    }
    async setLocalDescription(value) {
      this.localDescription = value;
    }
    async setRemoteDescription(value) {
      this.remoteDescription = value;
      this.connectionState = 'connected';
      this.onconnectionstatechange?.();
    }
    close() {
      this.connectionState = 'closed';
      this.onconnectionstatechange?.();
    }
  }
  const voice = new LiveVoice({
    browser: {
      canStartVoice: () => true,
      target: () => ({ mode: 'extension', target_id: 'grant' }),
      lockVoice: (active) => locks.push(active),
      voiceEvent() {},
    },
    audio: { srcObject: null, play: async () => {} },
    Peer,
    media: { getUserMedia: () => permission || Promise.resolve(stream) },
    onState: (state, message) => states.push({ state, message }),
    onEvent: (event) => events.push(event),
    onMedia: (role, stream) => mediaEvents.push({ role, stream }),
    request: async (path, options) => {
      calls.push({ path, body: JSON.parse(options.body) });
      if (path.endsWith('/session'))
        return rejectSession
          ? Response.json({ detail: 'Model unavailable' }, { status: 502 })
          : Response.json({ session_id: 'session-token', sdp: 'answer' });
      if (path.endsWith('/events'))
        return new Response(
          new ReadableStream({
            start(controller) {
              options.signal.addEventListener('abort', () =>
                controller.error(new DOMException('Aborted', 'AbortError')),
              );
            },
          }),
        );
      return Response.json({ status: 'closed' });
    },
  });
  return { voice, calls, states, locks, events, mediaEvents, track, stream };
}

test('voice pins the selected tab, streams events, mutes locally and releases resources', async () => {
  const f = fixture();
  await f.voice.start();
  assert.equal(f.voice.state, 'listening');
  assert.equal(f.calls[0].body.target_id, 'grant');
  f.voice.mute();
  assert.equal(f.track.enabled, false);
  f.voice.mute();
  assert.equal(f.track.enabled, true);
  await f.voice.stop();
  assert.ok(f.calls.some((c) => c.path.endsWith('/stop')));
  await f.voice.end();
  assert.equal(f.track.stopped, true);
  assert.deepEqual(f.locks, [true, false]);
  assert.equal(f.voice.session, null);
});

test('ending during the microphone permission prompt never opens a paid session', async () => {
  let resolve;
  const f = fixture({
    permission: new Promise((r) => {
      resolve = r;
    }),
  });
  const start = f.voice.start();
  await f.voice.end();
  resolve(f.stream);
  await start;
  assert.equal(f.track.stopped, true);
  assert.equal(f.calls.length, 0);
  assert.equal(f.voice.state, 'idle');
});

test('provider failure releases the microphone and existing browser controls', async () => {
  const f = fixture({ rejectSession: true });
  await f.voice.start();
  assert.equal(f.track.stopped, true);
  assert.equal(f.voice.state, 'idle');
  assert.equal(f.states.at(-1).message, 'Model unavailable');
  assert.equal(f.locks.at(-1), false);
});

test('repeated Start while connected never creates a second voice session', async () => {
  const f = fixture();
  await f.voice.start();
  await f.voice.start();
  assert.equal(f.calls.filter((c) => c.path.endsWith('/session')).length, 1);
  await f.voice.end();
});

test('presentation receives existing streams and ignores remote tracks after teardown', async () => {
  const f = fixture();
  await f.voice.start();
  const peer = f.voice.peer;
  peer.ontrack({ streams: [f.stream] });
  assert.deepEqual(
    f.mediaEvents.map((event) => event.role),
    ['user', 'assistant'],
  );
  assert.equal(f.mediaEvents[0].stream, f.stream);
  await f.voice.end();
  peer.ontrack({ streams: [f.stream] });
  assert.equal(f.mediaEvents.length, 2);
  assert.equal(f.voice.audio.srcObject, null);
});
