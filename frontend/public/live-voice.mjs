import { LivePanel } from './live-panel.mjs';
import { WakePhrase } from './live-wake.mjs';

/** Native audio session. Browser actions and credentials remain owned by the backend. */
export class LiveVoice {
  constructor({
    browser,
    onState,
    onEvent,
    onMedia = () => {},
    audio,
    media = navigator.mediaDevices,
    Peer = RTCPeerConnection,
    request = globalThis.fetch.bind(globalThis),
  }) {
    Object.assign(this, { browser, onState, onEvent, onMedia, audio, media, Peer, request });
    this.state = 'idle';
    this.generation = 0;
    this.session = null;
    this.muted = false;
  }

  setState(state, message) {
    this.state = state;
    this.onState(state, message);
  }

  async api(path, body, signal) {
    const response = await this.request(`/api/voice/${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal,
    });
    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.detail || error.error || 'Voice service unavailable');
    }
    return response;
  }

  async start(deviceId = '') {
    if (!['idle', 'error'].includes(this.state)) return;
    if (!this.browser.canStartVoice()) {
      this.setState(
        'error',
        'Choose an available browser below and finish the current command first.',
      );
      return;
    }
    const target = { ...this.browser.target() };
    const version = ++this.generation;
    this.browser.lockVoice(true);
    this.setState('connecting', 'Connecting your microphone…');
    this.controller = new AbortController();
    try {
      const stream = await this.media.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
          ...(deviceId ? { deviceId: { exact: deviceId } } : {}),
        },
      });
      if (version !== this.generation) {
        stream.getTracks().forEach((track) => track.stop());
        return;
      }
      this.stream = stream;
      this.onMedia('user', stream);
      for (const track of stream.getAudioTracks())
        track.onended = () => this.end('Microphone disconnected. Reconnect to continue.');
      const peer = new this.Peer();
      this.peer = peer;
      peer.ontrack = (event) => {
        if (version !== this.generation) return;
        this.audio.srcObject = event.streams[0];
        this.onMedia('assistant', event.streams[0]);
        this.audio.play().catch(() => {
          if (version === this.generation)
            this.onEvent({ type: 'playback_blocked', message: 'Press Play voice to hear PANINI.' });
        });
      };
      peer.onconnectionstatechange = () => {
        if (version !== this.generation) return;
        if (peer.connectionState === 'connected')
          this.setState('listening', 'Listening · Speak naturally, even while the browser works.');
        if (['failed', 'closed'].includes(peer.connectionState))
          this.end('Voice connection ended. Reconnect to continue.');
        if (peer.connectionState === 'disconnected') {
          this.setState('reconnecting', 'Audio connection interrupted…');
          clearTimeout(this.reconnectTimer);
          this.reconnectTimer = setTimeout(() => {
            if (peer.connectionState !== 'connected')
              this.end('Connection lost. Browser work stopped. Reconnect to continue.');
          }, 8000);
        }
      };
      stream.getTracks().forEach((track) => peer.addTrack(track, stream));
      // Events are authoritative on the sideband; the browser never executes model tool calls.
      this.channel = peer.createDataChannel('oai-events');
      await peer.setLocalDescription(await peer.createOffer());
      if (peer.iceGatheringState !== 'complete')
        await new Promise((resolve, reject) => {
          const done = () => {
            clearTimeout(timer);
            peer.removeEventListener('icegatheringstatechange', changed);
            resolve();
          };
          const changed = () => {
            if (peer.iceGatheringState === 'complete') done();
          };
          const timer = setTimeout(done, 2500);
          peer.addEventListener('icegatheringstatechange', changed);
          this.controller.signal.addEventListener(
            'abort',
            () => {
              done();
              reject(new DOMException('Aborted', 'AbortError'));
            },
            { once: true },
          );
        });
      if (version !== this.generation) return;
      const response = await this.api(
        'session',
        { ...target, sdp: peer.localDescription.sdp },
        AbortSignal.any([this.controller.signal, AbortSignal.timeout(55000)]),
      );
      const data = await response.json();
      if (version !== this.generation) {
        await this.api('end', { session_id: data.session_id }).catch(() => {});
        return;
      }
      this.session = data.session_id;
      this.eventsTask = this.readEvents(version);
      await peer.setRemoteDescription({ type: 'answer', sdp: data.sdp });
      this.connectTimer = setTimeout(() => {
        if (version === this.generation && peer.connectionState !== 'connected')
          this.end('Could not establish audio. Check your network and reconnect.');
      }, 20000);
    } catch (error) {
      if (version !== this.generation) return;
      await this.end(
        error.name === 'NotAllowedError'
          ? 'Allow microphone access to start a live conversation.'
          : error.message,
      );
    }
  }

  async readEvents(version) {
    try {
      const response = await this.api(
        'events',
        { session_id: this.session },
        this.controller.signal,
      );
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      try {
        while (version === this.generation) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          if (buffer.length > 1_000_000)
            throw new Error('Voice event stream exceeded its buffer limit');
          const lines = buffer.split('\n');
          buffer = lines.pop();
          for (const line of lines)
            if (line.trim()) {
              const event = JSON.parse(line);
              this.onEvent(event);
              if (event.type === 'browser') this.browser.voiceEvent(event.event);
              if (event.type === 'closed' || event.type === 'disconnected') {
                void this.end(event.message);
                return;
              }
            }
        }
      } finally {
        reader.releaseLock();
      }
      if (version === this.generation)
        void this.end('Voice event connection ended. Reconnect to continue.');
    } catch (error) {
      if (version === this.generation) void this.end(error.message);
    }
  }

  mute() {
    this.muted = !this.muted;
    this.stream?.getAudioTracks().forEach((track) => {
      track.enabled = !this.muted;
    });
    this.onEvent({ type: 'muted', muted: this.muted });
  }

  async stop() {
    if (!this.session) return;
    await this.api('stop', { session_id: this.session }, AbortSignal.timeout(15000));
  }

  async end(message = 'Conversation ended. Microphone off.') {
    if (this.state === 'ending') return;
    this.generation++;
    this.setState('ending', 'Ending conversation…');
    const token = this.session;
    this.session = null;
    clearTimeout(this.connectTimer);
    clearTimeout(this.reconnectTimer);
    this.stream?.getTracks().forEach((track) => {
      track.onended = null;
      track.stop();
    });
    this.stream = null;
    this.channel?.close();
    this.peer?.close();
    this.peer = null;
    this.audio.srcObject = null;
    this.muted = false;
    if (token)
      await this.api('end', { session_id: token }, AbortSignal.timeout(15000)).catch(() => {});
    this.controller?.abort();
    this.browser.lockVoice(false);
    this.setState('idle', message);
  }
}

if (typeof document !== 'undefined' && document.getElementById('live-start')) {
  const el = (id) => document.getElementById(id);
  const panel = new LivePanel(el('live-panel'));
  let wake,
    startPending = false;
  const live = new LiveVoice({
    browser: window.JevBrowser,
    audio: el('live-audio'),
    onState: (state, message) => {
      panel.connection(state, message);
      void wake?.sync();
    },
    onEvent: (event) => panel.event(event),
    onMedia: (role, stream) => panel.meter.attach(role, stream),
  });
  const startConversation = async () => {
    if (startPending || !['idle', 'error'].includes(live.state)) return;
    startPending = true;
    try {
      void wake.sync();
      if (!(await wake.release())) {
        wake.fail('Wake microphone did not close. Reload the page before trying again.');
        return;
      }
      await live.start(el('live-device').value);
      try {
        const current = el('live-device').value;
        const devices = await navigator.mediaDevices.enumerateDevices();
        el('live-device').replaceChildren(new Option('Default microphone', ''));
        devices
          .filter((d) => d.kind === 'audioinput')
          .forEach((d) => el('live-device').add(new Option(d.label || 'Microphone', d.deviceId)));
        el('live-device').value = current;
      } catch {}
    } finally {
      startPending = false;
      void wake.sync();
    }
  };
  let wakeLanguage;
  try {
    wakeLanguage = localStorage.getItem('jev-panini-wake-language');
  } catch {}
  wake = new WakePhrase({
    language: wakeLanguage,
    onWake: startConversation,
    onState: (status) => panel.wakeStatus(status),
    showDetails: () => el('live-wake-details').open,
    onDetail: (message) => panel.wakeDetail(message),
    getBlocker: () =>
      document.hidden
        ? 'Wake listening paused · Return to this page to resume.'
        : startPending || !['idle', 'error'].includes(live.state)
          ? 'Wake phrase paused during your live conversation.'
          : !window.JevBrowser.canStartVoice()
            ? 'Wake listening paused · Choose an available browser and finish any task or dictation.'
            : '',
  });
  el('live-wake-enabled').onchange = (event) => {
    void wake.enable(event.target.checked);
  };
  el('live-wake-language').onchange = (event) => {
    const language = event.target.value;
    try {
      localStorage.setItem('jev-panini-wake-language', language);
    } catch {}
    void wake.setLanguage(language);
  };
  const syncWake = () => {
    void wake.sync();
  };
  document.addEventListener('visibilitychange', syncWake);
  window.addEventListener('jev-browser-availability', syncWake);
  // Existing dictation waits until the wake recognizer has released the microphone.
  window.JevBrowser.prepareDictation = () => wake.release();
  el('live-start').onclick = startConversation;
  el('live-end').onclick = () => live.end();
  el('live-mute').onclick = () => live.mute();
  el('live-stop').onclick = async () => {
    panel.stopping = true;
    panel.render();
    try {
      await live.stop();
      panel.event({ type: 'task_stopped' });
    } catch (error) {
      panel.event({ type: 'error', message: error.message });
    } finally {
      panel.stopping = false;
      panel.render();
    }
  };
  el('live-play').onclick = async () => {
    panel.meter.prepare();
    try {
      await el('live-audio').play();
      panel.event({ type: 'playback_ready' });
    } catch {
      panel.event({
        type: 'playback_blocked',
        message:
          'Audio playback is blocked. Check your browser audio permissions, then try Play voice again.',
      });
    }
  };
  window.addEventListener('pagehide', () => {
    wake.dispose();
    document.removeEventListener('visibilitychange', syncWake);
    window.removeEventListener('jev-browser-availability', syncWake);
    delete window.JevBrowser.prepareDictation;
    panel.dispose();
    live.stream?.getTracks().forEach((track) => track.stop());
    if (live.session)
      navigator.sendBeacon(
        '/api/voice/end',
        new Blob([JSON.stringify({ session_id: live.session })], { type: 'application/json' }),
      );
  });
}
