import { test } from 'node:test';
import assert from 'node:assert/strict';
import { WakePhrase, isWakePhrase } from '../public/live-wake.mjs';

const flush = async () => {
  for (let i = 0; i < 12; i++) await Promise.resolve();
};
function fixture({
  delayedEnd = false,
  supported = true,
  onWake,
  autoStart = true,
  language,
  hints = false,
} = {}) {
  const recognizers = [],
    states = [],
    timers = new Map(),
    details = [];
  let detailsOpen = false;
  let clock = 0,
    id = 0,
    blocker = '',
    wakes = 0;
  class Recognition {
    constructor() {
      if (hints) this.phrases = [];
      recognizers.push(this);
    }
    start() {
      if (autoStart) this.onstart?.();
    }
    abort() {
      this.aborted = true;
      if (!delayedEnd) this.onend?.();
    }
    result(text, isFinal = true, alternatives = []) {
      const result = [text, ...alternatives].map((transcript) => ({ transcript }));
      result.isFinal = isFinal;
      this.onresult?.({ resultIndex: 0, results: [result] });
    }
    error(error) {
      this.onerror?.({ error });
      this.onend?.();
    }
  }
  class Phrase {
    constructor(phrase, boost) {
      Object.assign(this, { phrase, boost });
    }
  }
  const wake = new WakePhrase({
    Recognition: supported ? Recognition : null,
    Phrase,
    language,
    onWake: async () => {
      wakes++;
      await onWake?.();
    },
    onState: (state) => states.push(state),
    showDetails: () => detailsOpen,
    onDetail: (message) => details.push(message),
    getBlocker: () => blocker,
    now: () => clock,
    later: (fn, delay) => {
      const key = ++id;
      timers.set(key, { fn, at: clock + delay });
      return key;
    },
    cancel: (key) => timers.delete(key),
  });
  return {
    wake,
    states,
    recognizers,
    timers,
    details,
    showDetails: (open) => {
      detailsOpen = open;
    },
    get current() {
      return recognizers.at(-1);
    },
    get wakes() {
      return wakes;
    },
    get state() {
      return states.at(-1);
    },
    block: (reason) => {
      blocker = reason;
      return wake.sync();
    },
    advance: async (ms) => {
      clock += ms;
      for (const [key, timer] of [...timers]) {
        if (timer.at <= clock) {
          timers.delete(key);
          timer.fn();
        }
      }
      await flush();
    },
  };
}

test('wake phrase matches complete words within speech, including ASR spacing variants', () => {
  for (const text of [
    'Hey Panini',
    'HEY, PANINI!',
    'Hey Panini, start',
    '  hey   panini.',
    'Okay hey Panini',
    'hey pa nini',
    'hay panini',
    'Hey Paninee',
  ])
    assert.equal(isWakePhrase(text), true, text);
  for (const text of [
    'Panini',
    'Hey',
    'They panini',
    'Hey paninis',
    'hey banana',
    'hey pa',
    'hey pan',
    'hey paninilater',
  ])
    assert.equal(isWakePhrase(text), false, text);
});

test('wake listening requires explicit opt in and ignores incomplete or unrelated speech', async () => {
  const f = fixture();
  await f.wake.sync();
  assert.equal(f.recognizers.length, 0);
  await f.wake.enable(true);
  assert.equal(f.state.state, 'listening');
  assert.equal(f.current.continuous, true);
  assert.equal(f.current.interimResults, true);
  f.current.result('hey pan', false);
  f.current.result('tell me about sandwiches');
  await flush();
  assert.equal(f.wakes, 0);
  await f.wake.enable(false);
  assert.equal(f.current.aborted, true);
  assert.equal(f.state.state, 'off');
});

test('the user-reported Hai Pani Nahin transcription is a narrowly scoped wake alias', () => {
  for (const text of [' Hai Pani Nahin', 'HAI PANI NAHIN!', '“Hai, pani nahin.”'])
    assert.equal(isWakePhrase(text), true, text);
  for (const text of [
    'hai',
    'hai pani',
    'pani nahin',
    'hai pani nahinplease',
    'there is hai pani nahin',
    'hai pani nahin today',
  ])
    assert.equal(isWakePhrase(text), false, text);
});

test('the reported transcription starts one conversation after microphone release', async () => {
  for (const final of [true, false]) {
    const f = fixture({ delayedEnd: true });
    await f.wake.enable(true);
    f.current.result(' Hai Pani Nahin', final);
    if (!final) await f.advance(300);
    assert.equal(f.state.state, 'waking');
    assert.equal(f.current.aborted, true);
    assert.equal(f.wakes, 0);
    f.current.onend();
    await flush();
    assert.equal(f.wakes, 1);
  }
});

test('wake waits for microphone release and starts exactly once without forwarding speech', async () => {
  const f = fixture({ delayedEnd: true });
  await f.wake.enable(true);
  const previousResult = f.current.onresult;
  f.current.result('Hey Panini, private words');
  assert.equal(f.state.state, 'waking');
  assert.equal(f.current.aborted, true);
  assert.equal(f.wakes, 0);
  const result = [{ transcript: 'Hey Panini' }];
  result.isFinal = true;
  previousResult({ resultIndex: 0, results: [result] });
  f.current.onend();
  await flush();
  assert.equal(f.wakes, 1);
  assert.equal(JSON.stringify(f.states).includes('private words'), false);
});

test('two adjacent final speech segments can form the wake phrase but stale segments cannot', async () => {
  const f = fixture();
  await f.wake.enable(true);
  f.current.result('Hey.');
  await f.advance(2100);
  f.current.result('Panini');
  await flush();
  assert.equal(f.wakes, 0);
  f.current.result('Hey');
  await f.advance(500);
  f.current.result('Panini!');
  await flush();
  assert.equal(f.wakes, 1);
});

test('disabling during microphone handoff cancels wake activation', async () => {
  const f = fixture({ delayedEnd: true });
  await f.wake.enable(true);
  f.current.result('Hey Panini');
  const disabled = f.wake.enable(false);
  f.current.onend();
  await disabled;
  await flush();
  assert.equal(f.wakes, 0);
  assert.equal(f.state.state, 'off');
  assert.equal(f.timers.size, 0);
});

test('busy or hidden page pauses recognition and resumes only when available', async () => {
  const f = fixture();
  await f.wake.enable(true);
  await f.block('Conversation active');
  assert.equal(f.state.state, 'paused');
  assert.equal(f.current.aborted, true);
  const count = f.recognizers.length;
  await f.advance(10000);
  assert.equal(f.recognizers.length, count);
  await f.block('');
  assert.equal(f.recognizers.length, count + 1);
  assert.equal(f.state.state, 'listening');
  await f.block('Page hidden');
  assert.equal(f.state.state, 'paused');
  assert.equal(f.wakes, 0);
});

test('a blocker appearing during handoff prevents live activation', async () => {
  const f = fixture({ delayedEnd: true });
  await f.wake.enable(true);
  f.current.result('Hey Panini');
  await f.block('Page hidden');
  f.current.onend();
  await flush();
  assert.equal(f.wakes, 0);
  await f.advance(700);
  assert.equal(f.state.state, 'paused');
});

test('permission denial disables automatic retries and supports explicit retry', async () => {
  const f = fixture();
  await f.wake.enable(true);
  f.current.error('not-allowed');
  await flush();
  assert.equal(f.state.enabled, false);
  assert.match(f.state.message, /permission was denied/);
  await f.advance(30000);
  assert.equal(f.recognizers.length, 1);
  await f.wake.enable(true);
  assert.equal(f.state.state, 'listening');
});

test('network recovery is bounded and reports failure after three attempts', async () => {
  const f = fixture();
  await f.wake.enable(true);
  for (let i = 0; i < 3; i++) {
    f.current.error('network');
    await f.advance(8000);
  }
  assert.equal(f.recognizers.length, 3);
  assert.equal(f.state.state, 'error');
  assert.equal(f.state.enabled, false);
  assert.equal(f.timers.size, 0);
});

test('normal recognizer end restarts and disposal cancels the pending restart', async () => {
  const f = fixture();
  await f.wake.enable(true);
  await f.advance(6000);
  f.current.onend();
  await f.advance(500);
  assert.equal(f.recognizers.length, 2);
  f.current.error('no-speech');
  f.wake.dispose();
  await f.advance(5000);
  assert.equal(f.recognizers.length, 2);
  assert.equal(f.timers.size, 0);
});

test('recognizer shutdown timeout never starts live audio or a second recognizer', async () => {
  const f = fixture({ delayedEnd: true });
  await f.wake.enable(true);
  f.current.result('Hey Panini');
  await f.advance(2500);
  assert.equal(f.wakes, 0);
  assert.equal(f.state.state, 'error');
  assert.match(f.state.message, /did not close/);
  assert.equal(await f.wake.release(), false);
  await f.wake.enable(true);
  assert.equal(f.recognizers.length, 1);
});

test('rapid off and on waits for the prior microphone and preserves the latest choice', async () => {
  const f = fixture({ delayedEnd: true });
  await f.wake.enable(true);
  const off = f.wake.enable(false);
  const on = f.wake.enable(true);
  assert.equal(f.recognizers.length, 1);
  f.current.onend();
  await Promise.all([off, on]);
  assert.equal(f.state.state, 'listening');
  assert.equal(f.recognizers.length, 2);
});

test('unsupported browser stays disabled and offers the existing manual control', async () => {
  const f = fixture({ supported: false });
  await f.wake.enable(true);
  assert.equal(f.state.state, 'unsupported');
  assert.equal(f.state.enabled, false);
  assert.match(f.state.message, /Start conversation/);
  assert.equal(f.recognizers.length, 0);
});

test('stable interim wake recognition activates without waiting for a finalized sentence', async () => {
  const f = fixture();
  await f.wake.enable(true);
  f.current.result('Okay hey Panini', false);
  await f.advance(200);
  assert.equal(f.wakes, 0);
  f.current.result('Okay hey Panini could you', false);
  await f.advance(100);
  assert.equal(f.wakes, 1);
  assert.equal(f.current.aborted, true);
});

test('a revised interim hypothesis cancels pending wake detection', async () => {
  const f = fixture();
  await f.wake.enable(true);
  f.current.result('Hey Panini', false);
  await f.advance(100);
  f.current.result('Hey banana', false);
  await f.advance(400);
  assert.equal(f.wakes, 0);
});

test('recognition alternatives and split names trigger the full phrase only once', async () => {
  const f = fixture();
  await f.wake.enable(true);
  assert.equal(f.current.maxAlternatives, 5);
  f.current.result('hey banana', true, ['hey pa nini']);
  await flush();
  assert.equal(f.wakes, 1);
});

test('an interim name can complete a preceding final hey', async () => {
  const f = fixture();
  await f.wake.enable(true);
  f.current.result('Okay hey');
  f.current.result('Panini', false);
  await f.advance(300);
  assert.equal(f.wakes, 1);
});

test('disabling or hiding during an interim match never starts a conversation', async () => {
  for (const action of ['disable', 'hide']) {
    const f = fixture();
    await f.wake.enable(true);
    f.current.result('Hey Panini', false);
    if (action === 'disable') await f.wake.enable(false);
    else await f.block('Page hidden');
    await f.advance(500);
    assert.equal(f.wakes, 0);
  }
});

test('startup with no recognizer events fails visibly instead of staying stuck', async () => {
  const f = fixture({ autoStart: false });
  await f.wake.enable(true);
  assert.equal(f.state.state, 'starting');
  await f.advance(15000);
  assert.equal(f.state.state, 'error');
  assert.match(f.state.message, /did not start/);
  assert.equal(f.state.enabled, false);
  assert.equal(f.current.aborted, true);
});

test('live speech details are opt-in and never enter wake status messages', async () => {
  const f = fixture();
  await f.wake.enable(true);
  f.current.result('private words');
  assert.equal(f.details.length, 0);
  f.showDetails(true);
  f.current.onaudiostart();
  assert.match(f.details.at(-1), /microphone opened/);
  f.current.result('private words');
  assert.match(f.details.at(-1), /Heard: “private words”/);
  assert.equal(JSON.stringify(f.states).includes('private words'), false);
  f.showDetails(false);
  const count = f.details.length;
  f.current.result('other private words');
  assert.equal(f.details.length, count);
});

test('recognition explicitly requests the chosen English locale and never accepts Hindi configuration', async () => {
  for (const language of ['en-US', 'en-IN', 'en-GB', 'en-AU']) {
    const f = fixture({ language });
    await f.wake.enable(true);
    assert.equal(f.current.lang, language);
    assert.equal(f.state.language, language);
    await f.wake.enable(false);
  }
  const f = fixture({ language: 'hi-IN' });
  await f.wake.enable(true);
  assert.equal(f.current.lang, 'en-US');
  await f.wake.setLanguage('hi-IN');
  assert.equal(f.current.lang, 'en-US');
});

test('changing English variant releases the old recognizer before opening the new one', async () => {
  const f = fixture({ delayedEnd: true });
  await f.wake.enable(true);
  const old = f.current;
  const changing = f.wake.setLanguage('en-IN');
  assert.equal(f.recognizers.length, 1);
  assert.equal(old.aborted, true);
  old.onend();
  await changing;
  assert.equal(f.current.lang, 'en-IN');
  assert.equal(f.recognizers.length, 2);
  assert.equal(f.wakes, 0);
});

test('language preferences never enable a microphone after disabling or while paused', async () => {
  const f = fixture({ delayedEnd: true });
  await f.wake.setLanguage('en-IN');
  assert.equal(f.recognizers.length, 0);
  await f.wake.enable(true);
  const changing = f.wake.setLanguage('en-GB');
  const disabling = f.wake.enable(false);
  f.current.onend();
  await Promise.all([changing, disabling]);
  assert.equal(f.recognizers.length, 1);
  assert.equal(f.state.enabled, false);
  await f.block('Conversation active');
  await f.wake.enable(true);
  await f.wake.setLanguage('en-IN');
  assert.equal(f.recognizers.length, 1);
  assert.equal(f.state.state, 'paused');
});

test('supported recognition receives only the PANINI name hints', async () => {
  const f = fixture({ hints: true });
  await f.wake.enable(true);
  assert.deepEqual(
    f.current.phrases.map((p) => p.phrase),
    ['Hey Panini', 'Panini'],
  );
  assert.equal(f.state.nameHints, true);
});

test('services rejecting phrase hints retry once without the experimental feature', async () => {
  const f = fixture({ hints: true });
  await f.wake.enable(true);
  f.current.error('phrases-not-supported');
  await flush();
  await f.advance(500);
  assert.equal(f.recognizers.length, 2);
  assert.deepEqual(f.current.phrases, []);
  assert.equal(f.state.nameHints, false);
  assert.equal(f.state.state, 'listening');
  f.current.error('phrases-not-supported');
  await flush();
  assert.equal(f.state.enabled, false);
});
