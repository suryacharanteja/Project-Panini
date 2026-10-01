/** Read-only audio metering. Never captures, routes to speakers, or stops session tracks. */
export class LiveAudioMeter {
  constructor({
    audio,
    onSample,
    Context = globalThis.AudioContext || globalThis.webkitAudioContext,
    requestFrame = globalThis.requestAnimationFrame?.bind(globalThis),
    cancelFrame = globalThis.cancelAnimationFrame?.bind(globalThis),
  }) {
    Object.assign(this, { audio, onSample, Context, requestFrame, cancelFrame });
    this.sources = {};
    this.enabled = false;
    this.visible = true;
    this.muted = false;
    this.lastVoice = -Infinity;
    this.lastFrame = -Infinity;
    this.frame = null;
  }

  prepare() {
    if (!this.Context) return;
    try {
      this.context ||= new this.Context();
      // Called from Start / Play voice so autoplay policy can allow this analyser.
      this.context.resume().catch(() => {});
    } catch {
      // Visual enhancement is optional; the established audio transport continues.
    }
  }

  attach(role, stream) {
    if (!['user', 'assistant'].includes(role)) return;
    this.disconnect(role);
    if (!stream || !this.context) return;
    try {
      const analyser = this.context.createAnalyser();
      analyser.fftSize = 512;
      analyser.smoothingTimeConstant = 0.65;
      const source = this.context.createMediaStreamSource(stream);
      source.connect(analyser); // Intentionally no connection to the audio destination.
      this.sources[role] = {
        source,
        analyser,
        time: new Float32Array(analyser.fftSize),
        frequency: new Uint8Array(analyser.frequencyBinCount),
      };
      this.schedule();
    } catch {
      this.disconnect(role);
    }
  }

  disconnect(role) {
    const entry = this.sources[role];
    if (!entry) return;
    entry.source.disconnect();
    entry.analyser.disconnect();
    delete this.sources[role];
  }

  configure({ enabled = this.enabled, muted = this.muted, visible = this.visible } = {}) {
    Object.assign(this, { enabled, muted, visible });
    if (!enabled || !visible) {
      if (this.frame !== null) this.cancelFrame?.(this.frame);
      this.frame = null;
      this.lastVoice = -Infinity;
      this.onSample({ speaking: false, energy: 0, bands: [] });
    } else this.schedule();
  }

  read(role) {
    const entry = this.sources[role];
    if (!entry || this.context?.state !== 'running') return { level: 0, bands: [] };
    if (role === 'user' && this.muted) return { level: 0, bands: [] };
    if (role === 'assistant' && (this.audio.paused || this.audio.muted || !this.audio.volume))
      return { level: 0, bands: [] };
    entry.analyser.getFloatTimeDomainData(entry.time);
    const rms = Math.sqrt(
      entry.time.reduce((sum, value) => sum + value * value, 0) / entry.time.length,
    );
    if (rms < 0.004) return { level: 0, bands: [] };
    entry.analyser.getByteFrequencyData(entry.frequency);
    const bands = Array.from({ length: 22 }, (_, index) => {
      const bin = 1 + Math.floor((index * (entry.frequency.length / 3)) / 22);
      return entry.frequency[bin] / 255;
    });
    return { level: Math.min(1, rms * 9), bands };
  }

  schedule() {
    if (
      this.frame !== null ||
      !this.enabled ||
      !this.visible ||
      !this.context ||
      !this.requestFrame
    )
      return;
    this.frame = this.requestFrame((time) => {
      this.frame = null;
      if (time - this.lastFrame >= 33) {
        this.lastFrame = time;
        try {
          const input = this.read('user');
          const output = this.read('assistant');
          if (output.level > 0.045) this.lastVoice = time;
          if (this.audio.paused || this.audio.muted || !this.audio.volume)
            this.lastVoice = -Infinity;
          const speaking = time - this.lastVoice < 180;
          const active = speaking ? output : input;
          this.onSample({ speaking, energy: active.level, bands: active.bands });
        } catch {
          this.onSample({ speaking: false, energy: 0, bands: [] });
        }
      }
      this.schedule();
    });
  }

  stop() {
    this.configure({ enabled: false });
    for (const role of Object.keys(this.sources)) this.disconnect(role);
    const context = this.context;
    this.context = null;
    this.lastFrame = -Infinity;
    if (context && context.state !== 'closed') context.close().catch(() => {});
  }
}
