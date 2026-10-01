/** Optional wake detection. Recognized speech stays out of captions, logs and task execution. */
function normalize(text) {
  return String(text)
    .normalize('NFKC')
    .toLowerCase()
    .replace(/[^\p{L}\p{N}]+/gu, ' ')
    .trim();
}

export const WAKE_LANGUAGES = Object.freeze({
  'en-US': 'English (US)',
  'en-IN': 'English (India)',
  'en-GB': 'English (UK)',
  'en-AU': 'English (Australia)',
});

const validLanguage = (language) => Object.hasOwn(WAKE_LANGUAGES, language);

export function isWakePhrase(text) {
  const words = normalize(text);
  // A wake phrase can follow a hesitation or earlier speech in the same ASR turn.
  // Keep whole-word boundaries; never use broad substring/fuzzy name matching.
  const canonical = /(?:^|\s)(?:hey|hay)\s+(?:panini|pa nini|paninee)(?:\s|$)/u.test(words);
  // Confirmed by spoken UAT: this is how the recognizer transcribed “Hey Panini”.
  // Require the entire observed phrase so ordinary “pani” speech cannot wake PANINI.
  return canonical || words === 'hai pani nahin';
}

export class WakePhrase {
  constructor({
    Recognition = globalThis.SpeechRecognition || globalThis.webkitSpeechRecognition,
    Phrase = globalThis.SpeechRecognitionPhrase,
    language = 'en-US',
    getBlocker = () => '',
    onWake,
    onState,
    onDetail = () => {},
    showDetails = () => false,
    later = (callback, delay) => setTimeout(callback, delay),
    cancel = (timer) => clearTimeout(timer),
    now = Date.now,
  }) {
    Object.assign(this, {
      Recognition,
      Phrase,
      getBlocker,
      onWake,
      onState,
      onDetail,
      showDetails,
      later,
      cancel,
      now,
    });
    this.language = validLanguage(language) ? language : 'en-US';
    this.enabled = false;
    this.generation = 0;
    this.failures = 0;
    this.state = Recognition ? 'off' : 'unsupported';
    this.publish(
      this.state,
      Recognition
        ? 'Wake phrase is off.'
        : 'Wake phrase is unavailable in this browser. Use Start conversation, or open the app in Chrome.',
    );
  }

  publish(state, message) {
    this.state = state;
    this.message = message;
    this.onState({
      state,
      message,
      enabled: this.enabled,
      supported: !!this.Recognition,
      language: this.language,
      nameHints: !!this.nameHints,
    });
  }

  detail(message) {
    if (this.showDetails()) this.onDetail(message);
  }

  clearListeningTimers() {
    this.cancel(this.startTimer);
    this.cancel(this.matchTimer);
    this.startTimer = this.matchTimer = null;
  }

  async setLanguage(language) {
    if (!validLanguage(language) || language === this.language) return;
    this.language = language;
    const generation = ++this.generation;
    this.cancel(this.timer);
    this.timer = null;
    this.publish(
      this.enabled ? 'starting' : this.state,
      this.enabled ? `Switching wake recognition to ${WAKE_LANGUAGES[language]}…` : this.message,
    );
    const released = await this.release();
    if (generation !== this.generation || !this.enabled) return;
    if (!released)
      return this.fail('Wake microphone did not close. Reload the page before trying again.');
    await this.sync();
  }

  async enable(enabled) {
    const generation = ++this.generation;
    this.cancel(this.timer);
    this.timer = null;
    this.enabled = !!enabled && !!this.Recognition;
    this.failures = 0;
    if (!this.enabled) {
      const released = await this.release();
      if (generation !== this.generation) return;
      if (!released)
        return this.fail('Wake microphone did not close. Reload the page before trying again.');
      this.publish(
        this.Recognition ? 'off' : 'unsupported',
        this.Recognition
          ? 'Wake phrase is off.'
          : 'Wake phrase is unavailable in this browser. Use Start conversation, or open the app in Chrome.',
      );
      return;
    }
    await this.sync();
  }

  async sync() {
    if (!this.enabled || this.triggering) return;
    const reason = this.getBlocker();
    if (reason) {
      this.cancel(this.timer);
      this.timer = null;
      this.publish('paused', reason);
      await this.release();
      return;
    }
    if (this.recognition || this.starting || this.timer) return;
    this.starting = true;
    const generation = this.generation;
    const released = await this.release();
    this.starting = false;
    if (!this.enabled || generation !== this.generation) return;
    if (!released)
      return this.fail('Wake microphone did not close. Reload the page before trying again.');
    if (this.getBlocker()) return this.sync();
    this.listen();
  }

  listen() {
    let recognition;
    try {
      recognition = new this.Recognition();
      recognition.lang = this.language;
      recognition.continuous = true;
      recognition.interimResults = true;
      recognition.maxAlternatives = 5;
    } catch {
      return this.fail('Wake phrase could not start in this browser. Use Start conversation.');
    }
    this.nameHints = false;
    // Contextual biasing is experimental: use it only when exposed, and recover
    // if the selected recognition service rejects it. Never request Hindi or install packs.
    if (!this.biasUnavailable && this.Phrase && 'phrases' in recognition) {
      try {
        recognition.phrases = [new this.Phrase('Hey Panini', 5), new this.Phrase('Panini', 5)];
        this.nameHints = true;
      } catch {
        this.biasUnavailable = true;
      }
    }
    this.recognition = recognition;
    this.lastHey = -Infinity;
    let error = '',
      startedAt = this.now();
    const current = () => this.enabled && this.recognition === recognition;
    recognition.onstart = () => {
      if (!current()) return;
      this.cancel(this.startTimer);
      this.startTimer = null;
      this.publish(
        'listening',
        `Wake listening on · ${WAKE_LANGUAGES[this.language]} · Say “Hey Panini”.`,
      );
      this.detail('Recognition connected. Waiting for speech from your default microphone.');
    };
    recognition.onaudiostart = () => {
      if (current()) this.detail('Default microphone opened. Say “Hey Panini”.');
    };
    recognition.onspeechstart = () => {
      if (current()) this.detail('Speech detected. Checking for “Hey Panini”…');
    };
    recognition.onnomatch = () => {
      if (current())
        this.detail(
          'Audio received, but no words recognized. Check your default microphone and speak clearly.',
        );
    };
    recognition.onresult = (event) => {
      if (!current()) return;
      if (this.getBlocker()) {
        void this.sync();
        return;
      }
      let partialMatch = false;
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const result = event.results[i];
        const candidates = Array.from(result)
          .slice(0, 5)
          .map((item) => String(item.transcript || '').slice(-500));
        this.detail(
          `${WAKE_LANGUAGES[this.language]} · ${result.isFinal ? 'Heard' : 'Hearing'}: “${(candidates[0] || '').slice(-160)}”`,
        );
        const match = candidates.some(
          (text) =>
            isWakePhrase(text) || (this.now() - this.lastHey < 2000 && isWakePhrase(`hey ${text}`)),
        );
        this.failures = 0;
        if (result.isFinal) {
          this.lastHey = candidates.some((text) => /(?:^|\s)(?:hey|hay)$/.test(normalize(text)))
            ? this.now()
            : -Infinity;
        }
        if (match && result.isFinal) {
          void this.activate();
          return;
        }
        partialMatch ||= match;
      }
      if (!partialMatch) {
        this.cancel(this.matchTimer);
        this.matchTimer = null;
      } else if (!this.matchTimer) {
        // ASR can keep an utterance interim while the user continues talking.
        // Allow a short stable match, cancelling it when the hypothesis changes.
        this.matchTimer = this.later(() => {
          this.matchTimer = null;
          if (current()) void this.activate();
        }, 300);
      }
    };
    recognition.onerror = (event) => {
      if (!current()) return;
      error = event.error;
      if (error === 'phrases-not-supported' && this.nameHints) {
        this.biasUnavailable = true;
        this.nameHints = false;
        this.publish(
          'restarting',
          'Name hints are unavailable. Reconnecting with your selected English language…',
        );
        void this.release().then((released) => {
          if (!this.enabled) return;
          if (!released)
            return this.fail('Wake microphone did not close. Reload the page before trying again.');
          this.schedule(500);
        });
        return;
      }
      const messages = {
        'not-allowed':
          'Microphone permission was denied. Allow it in browser settings, then enable the wake phrase again.',
        'service-not-allowed':
          'This browser does not allow speech recognition. Use Start conversation or try Chrome.',
        'audio-capture':
          'No microphone is available for wake listening. Check your default microphone, then try again.',
        'language-not-supported':
          'This English variant is unavailable. Choose another wake recognition language and enable it again.',
        'phrases-not-supported':
          'Speech recognition could not start without name hints. Use Start conversation.',
      };
      if (messages[error]) this.fail(messages[error]);
    };
    recognition.onend = () => {
      if (!current()) return;
      this.clearListeningTimers();
      this.recognition = null;
      if ((error && error !== 'no-speech') || (!error && this.now() - startedAt < 1000))
        this.failures++;
      if (this.failures >= 3)
        return this.fail(
          'Wake recognition service could not stay connected. Try this page in Chrome, check the connection, or use Start conversation.',
        );
      this.publish('restarting', 'Reconnecting wake listening…');
      this.schedule(error && error !== 'no-speech' ? 1000 * 2 ** this.failures : 500);
    };
    this.publish('starting', 'Starting wake listening · Allow microphone access if asked.');
    this.startTimer = this.later(() => {
      if (current())
        this.fail(
          'The browser did not start wake recognition. Allow microphone access, or open this page in Chrome and try again. Start conversation is also available.',
        );
    }, 15000);
    try {
      recognition.start();
    } catch {
      this.fail(
        'Wake listening could not start. Check microphone permission or use Start conversation.',
      );
    }
  }

  schedule(delay) {
    this.cancel(this.timer);
    this.timer = this.later(() => {
      this.timer = null;
      void this.sync();
    }, delay);
  }

  /** Wait for recognizer shutdown before the live session claims the microphone. */
  release() {
    this.clearListeningTimers();
    if (this.closing) return this.closing;
    const recognition = this.recognition;
    if (!recognition) return Promise.resolve(!this.releaseFailed);
    this.recognition = null;
    recognition.onresult = recognition.onstart = recognition.onerror = null;
    recognition.onaudiostart = recognition.onspeechstart = recognition.onnomatch = null;
    this.closing = new Promise((resolve) => {
      let finished = false;
      const finish = (released) => {
        if (finished) return;
        finished = true;
        if (!released) this.releaseFailed = true;
        this.cancel(timeout);
        recognition.onend = null;
        resolve(released);
      };
      const timeout = this.later(() => finish(false), 2500);
      recognition.onend = () => finish(true);
      try {
        recognition.abort();
      } catch {
        finish(false);
      }
    }).then((released) => {
      this.closing = null;
      return released;
    });
    return this.closing;
  }

  async activate() {
    if (this.triggering || !this.enabled || this.getBlocker()) return;
    this.triggering = true;
    const generation = this.generation;
    this.publish('waking', '“Hey Panini” heard · Starting your conversation…');
    try {
      const released = await this.release();
      if (!this.enabled || generation !== this.generation) return;
      if (!released)
        return this.fail('Wake microphone did not close. Reload the page before trying again.');
      if (!this.getBlocker()) await this.onWake();
    } catch {
      this.fail('Could not start PANINI. Use Start conversation to try again.');
    } finally {
      this.triggering = false;
      if (this.enabled) this.schedule(700);
    }
  }

  fail(message) {
    this.enabled = false;
    this.generation++;
    this.cancel(this.timer);
    this.timer = null;
    void this.release();
    this.publish('error', message);
  }

  dispose() {
    this.enabled = false;
    this.generation++;
    this.cancel(this.timer);
    this.timer = null;
    void this.release();
  }
}
