import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import { test } from 'node:test';
import assert from 'node:assert/strict';

function fixture({
  onStatus,
  onExtension,
  getUserMedia,
  MediaRecorder,
  savedMode,
  mode = 'chromium',
} = {}) {
  let initialize;
  const storage = new Map(savedMode ? [['jev-browser-mode', savedMode]] : []);
  const elements = new Map();
  const element = (id) => {
    if (!elements.has(id))
      elements.set(id, {
        textContent: '',
        innerHTML: '',
        value: id === 'run-mode' ? mode : '',
        disabled: false,
        style: {},
        dataset: {},
        replaceChildren() {},
        append() {},
      });
    return elements.get(id);
  };
  const streams = [];
  let speech;
  class SpeechRecognition {
    constructor() {
      speech = this;
    }
    start() {}
    stop() {}
    abort() {}
  }
  const context = vm.createContext({
    document: {
      getElementById: element,
      createElement: () => element(crypto.randomUUID()),
      addEventListener(_event, callback) {
        initialize = callback;
      },
    },
    window: { SpeechRecognition, MediaRecorder, addEventListener() {} },
    MediaRecorder,
    Blob,
    FormData,
    navigator: { mediaDevices: { getUserMedia } },
    TextDecoder,
    AbortController,
    AbortSignal,
    setTimeout,
    clearTimeout,
    setInterval: () => 1,
    clearInterval() {},
    localStorage: {
      getItem: (key) => storage.get(key),
      setItem: (key, value) => storage.set(key, value),
    },
    location: { host: 'localhost:3000' },
    console,
    fetch: async (url, options) => {
      if (url.endsWith('/run-stream')) {
        const body = new ReadableStream({
          start(controller) {
            streams.push({ ...JSON.parse(options.body), controller });
            options.signal.addEventListener('abort', () =>
              controller.error(new DOMException('Aborted', 'AbortError')),
            );
          },
        });
        return new Response(body);
      }
      if (url.endsWith('/extension/status'))
        return Response.json(onExtension ? await onExtension() : { connected: false });
      if (url.startsWith('/api/browser/status') && onStatus) return Response.json(await onStatus());
      return Response.json({ stats: {}, status: 'stopping' });
    },
  });
  vm.runInContext(
    readFileSync(new URL('../public/browser-use.js', import.meta.url), 'utf8'),
    context,
  );
  return {
    elements,
    element,
    streams,
    run: (code) => vm.runInContext(code, context),
    speech: () => speech,
    initialize: () => initialize(),
    storage,
  };
}
const flush = () => new Promise((resolve) => setImmediate(resolve));
function finish(stream, event) {
  stream.controller.enqueue(new TextEncoder().encode(JSON.stringify(event) + '\n'));
  stream.controller.close();
}

test('incomplete browser result stays incomplete and controls recover', async () => {
  const f = fixture();
  f.run("enqueue('test')");
  await flush();
  finish(f.streams[0], { event: 'incomplete', message: 'Step limit reached' });
  await flush();
  assert.equal(f.element('status-text').textContent, 'incomplete: Step limit reached');
  assert.equal(f.element('process-btn').disabled, false);
});

test('no browser opens until a target is explicitly chosen', async () => {
  const f = fixture({ mode: '' });
  f.initialize();
  await flush();
  f.run("enqueue('open a page')");
  assert.equal(f.streams.length, 0);
  assert.equal(f.element('process-btn').disabled, true);
  assert.match(f.element('status-text').textContent, /Choose/);
});

test('extension choice survives reload and unavailable attachments never fall back', async () => {
  let connected = true;
  const f = fixture({
    mode: '',
    savedMode: 'extension',
    onExtension: () => ({
      connected,
      protocol: 2,
      compatible: true,
      attached_tab: 7,
      target_id: 'grant-a',
      tab_title: 'My page',
      tab_url: 'https://example.com/',
    }),
  });
  f.initialize();
  await flush();
  assert.equal(f.element('run-mode').value, 'extension');
  assert.match(f.element('extension-state').textContent, /My page/);
  f.run("enqueue('first'); enqueue('second')");
  assert.equal(f.streams[0].mode, 'extension');
  assert.equal(f.streams[0].target_id, 'grant-a');
  connected = false;
  finish(f.streams[0], { event: 'error', error: 'Extension disconnected' });
  await flush();
  assert.equal(f.streams.length, 1);
  assert.equal(f.element('process-btn').disabled, true);
  f.run("enqueue('third')");
  assert.equal(f.streams.length, 1);
  f.element('run-mode').value = 'chromium';
  f.run('onModeChanged()');
  assert.equal(f.storage.get('jev-browser-mode'), 'chromium');
  assert.equal(f.element('process-btn').disabled, false);
});

test('old extension version stays connected but Run requires an update', async () => {
  const f = fixture({
    mode: 'extension',
    onExtension: () => ({
      connected: true,
      protocol: 2,
      compatible: false,
      attached_tab: 7,
      target_id: 'old',
    }),
  });
  f.initialize();
  await flush();
  assert.equal(f.element('process-btn').disabled, true);
  assert.match(f.element('extension-state').textContent, /Reload JevRev Claw 3\.0/);
  assert.match(f.element('run-readiness').textContent, /Update JevRev Claw to 3\.0/);
  f.run("enqueue('open AI jobs search')");
  assert.equal(f.streams.length, 0);
});

test('Run explains a missing connection at this address and recovers after attachment', async () => {
  let state = { connected: false };
  const f = fixture({ mode: 'extension', onExtension: () => state });
  f.initialize();
  await flush();
  assert.equal(f.element('process-btn').disabled, true);
  assert.match(f.element('run-readiness').textContent, /not connected\. Pair it once/);
  state = { connected: true, protocol: 2, compatible: true, attached_tab: null };
  await f.run('refreshExtensionState()');
  assert.match(f.element('run-readiness').textContent, /Attach selected tab/);
  state = { ...state, attached_tab: 7, target_id: 'fresh-grant' };
  await f.run('refreshExtensionState()');
  assert.equal(f.element('process-btn').disabled, false);
  assert.match(f.element('run-readiness').textContent, /Ready on localhost:3000.*until complete/);
  f.element('voice-input').value = 'open the demo';
  f.element('process-btn').onclick();
  await flush();
  assert.equal(f.streams[0].target_id, 'fresh-grant');
  assert.equal(f.streams[0].run_policy, 'continuous');
  assert.match(f.element('run-readiness').textContent, /instruction is running/);
  finish(f.streams[0], { event: 'complete' });
  await flush();
  assert.equal(f.element('process-btn').disabled, false);
});

test('Run distinguishes an unreachable service from a missing attachment', async () => {
  const f = fixture({
    mode: 'extension',
    onExtension: () => {
      throw new Error('offline');
    },
  });
  f.initialize();
  await flush();
  assert.equal(f.element('process-btn').disabled, true);
  assert.match(f.element('run-readiness').textContent, /Cannot reach the extension service/);
});

test('continuous is default and each queued command retains its chosen run limits', async () => {
  const f = fixture();
  f.initialize();
  await flush();
  f.run("enqueue('long task')");
  assert.equal(f.streams[0].run_policy, 'continuous');
  assert.equal(f.streams[0].max_steps, undefined);
  assert.equal(f.streams[0].timeout_seconds, undefined);
  f.element('run-policy').value = '100';
  f.run("onRunPolicyChanged(); enqueue('bounded task')");
  assert.equal(f.storage.get('jev-run-policy'), '100');
  f.element('run-policy').value = '25';
  finish(f.streams[0], { event: 'complete' });
  await flush();
  assert.equal(f.streams[1].max_steps, 100);
  assert.equal(f.streams[1].timeout_seconds, 900);
  finish(f.streams[1], { event: 'complete' });
  await flush();
});

test('voice commands run sequentially and Stop discards pending work', async () => {
  const f = fixture();
  f.run("enqueue('first'); enqueue('second')");
  await flush();
  assert.equal(f.streams.length, 1);
  finish(f.streams[0], { event: 'complete', message: 'Done' });
  await flush();
  assert.equal(f.streams[1].instruction, 'second');
  f.run("enqueue('third')");
  await f.run('stopAll()');
  await flush();
  assert.equal(f.streams.length, 2);
  assert.equal(f.element('process-btn').disabled, false);
});

test('interim speech is visible but only a final utterance executes', async () => {
  const f = fixture();
  f.run('startWebSpeech()');
  const partial = [{ transcript: 'Open the demo' }];
  partial.isFinal = false;
  f.speech().onresult({ resultIndex: 0, results: [partial] });
  assert.equal(f.streams.length, 0);
  assert.match(f.element('stt-debug').textContent, /Open the demo/);
  partial.isFinal = true;
  f.speech().onresult({ resultIndex: 0, results: [partial] });
  await flush();
  assert.equal(f.streams.length, 1);
  finish(f.streams[0], { event: 'complete', message: 'Done' });
  await flush();
});

test('Stop keeps controls disabled until backend cleanup is confirmed', async () => {
  let release;
  const idle = new Promise((resolve) => {
    release = resolve;
  });
  const f = fixture({ onStatus: () => idle });
  f.run("enqueue('first')");
  await flush();
  const stopped = f.run('stopAll()');
  await flush();
  assert.equal(f.element('process-btn').disabled, true);
  assert.equal(f.element('reset-btn').disabled, true);
  release({ active: false, stats: { browser_started: true } });
  await stopped;
  await flush();
  assert.equal(f.element('process-btn').disabled, false);
  assert.equal(f.element('browser-mode').textContent, 'Visible Chromium ready');
});

test('late Web Speech callbacks cannot stop or execute in a newer recording session', async () => {
  const f = fixture();
  f.run('startWebSpeech()');
  const old = f.speech();
  f.run('stopRecording(true); stopping = false; startWebSpeech()');
  const final = [{ transcript: 'stale command' }];
  final.isFinal = true;
  old.onresult({ resultIndex: 0, results: [final] });
  old.onend();
  assert.equal(f.streams.length, 0);
  assert.equal(f.element('record-btn').textContent, 'Stop recording');
  f.run('stopRecording(true)');
});

test('a stale microphone permission result releases only its own stream', async () => {
  const pending = [],
    captured = [];
  class Recorder {
    static isTypeSupported() {
      return true;
    }
    constructor(stream) {
      this.stream = stream;
      this.state = 'inactive';
      captured.push(this);
    }
    start() {
      this.state = 'recording';
    }
    stop() {
      this.state = 'inactive';
    }
  }
  const audio = () => {
    const track = {
      stopped: false,
      stop() {
        this.stopped = true;
      },
    };
    return { track, getTracks: () => [track] };
  };
  const f = fixture({
    MediaRecorder: Recorder,
    getUserMedia: () => new Promise((resolve) => pending.push(resolve)),
  });
  f.run('sttConfigured = true');
  const first = f.run("toggleRecording('assemblyai')");
  await flush();
  f.run('stopRecording(true)');
  const second = f.run("toggleRecording('assemblyai')");
  await flush();
  const fresh = audio(),
    stale = audio();
  pending[1](fresh);
  await second;
  pending[0](stale);
  await first;
  assert.equal(stale.track.stopped, true);
  assert.equal(fresh.track.stopped, false);
  assert.equal(captured.length, 1);
  assert.equal(captured[0].stream, fresh);
  f.run('stopRecording(true)');
});

test('dictation waits for wake microphone release before starting Web Speech', async () => {
  const f = fixture();
  f.run(
    'let releaseWake; window.JevBrowser.prepareDictation = () => new Promise(resolve => { releaseWake = resolve; })',
  );
  const start = f.run('startWebSpeech()');
  assert.equal(f.speech(), undefined);
  assert.equal(f.run('window.JevBrowser.canStartVoice()'), false);
  f.run('releaseWake(true)');
  await start;
  assert.ok(f.speech());
  f.run('stopRecording(true)');
});

test('Stop while waiting for wake microphone prevents delayed dictation capture', async () => {
  let captures = 0;
  const f = fixture({
    MediaRecorder: class {},
    getUserMedia: () => {
      captures++;
      throw new Error('Unexpected capture');
    },
  });
  f.run(
    'let releaseWake; window.JevBrowser.prepareDictation = () => new Promise(resolve => { releaseWake = resolve; })',
  );
  for (const command of ['startWebSpeech()', "toggleRecording('assemblyai')"]) {
    f.run('sttConfigured = true');
    const start = f.run(command);
    f.run('stopRecording(true); releaseWake(true)');
    await start;
  }
  assert.equal(f.speech(), undefined);
  assert.equal(captures, 0);
});

test('failed wake microphone release blocks dictation with a visible error', async () => {
  const f = fixture();
  f.run('window.JevBrowser.prepareDictation = async () => false');
  await f.run('startWebSpeech()');
  assert.equal(f.speech(), undefined);
  assert.match(f.element('stt-debug').textContent, /Wake microphone did not close/);
  assert.equal(f.run('recording'), false);
});
