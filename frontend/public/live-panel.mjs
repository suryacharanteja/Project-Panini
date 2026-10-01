import { LiveAudioMeter } from './live-audio.mjs';
import { initialPresence, presenceActivity, taskLabels, updatePresence } from './live-presence.mjs';

const motionKey = 'jev-panini-reduced-motion';
const clipCaption = (value) => (value.length > 320 ? `…${value.slice(-320)}` : value);

/** UI adapter: renders existing session events; never issues a browser or voice command. */
export class LivePanel {
  constructor(root, { Meter = LiveAudioMeter } = {}) {
    this.root = root;
    this.document = root.ownerDocument;
    this.el = (id) => this.document.getElementById(id);
    this.state = initialPresence();
    this.wake = { state: 'off', enabled: false };
    this.el('live-wake-details').ontoggle = () => {
      this.text('live-wake-heard', 'Say “Hey Panini” while wake listening is on.');
    };
    this.el('live-wake-settings-toggle').onclick = () => {
      const settings = this.el('live-wake-settings');
      settings.hidden = !settings.hidden;
      this.el('live-wake-settings-toggle').setAttribute('aria-expanded', String(!settings.hidden));
      if (settings.hidden) {
        this.el('live-wake-details').open = false;
        this.text('live-wake-heard', 'Say “Hey Panini” while wake listening is on.');
      }
    };
    this.rows = [];
    this.lastTime = -1;
    this.stopping = false;
    const lanes = Array.from({ length: 2 }, () => {
      const lane = this.document.createElement('div');
      lane.className = 'live-wave-side';
      this.el('live-wave').append(lane);
      return lane;
    });
    this.bars = Array.from({ length: 44 }, (_, index) => {
      const bar = this.document.createElement('span');
      lanes[index < 22 ? 0 : 1].append(bar);
      return bar;
    });
    this.meter = new Meter({
      audio: this.el('live-audio'),
      onSample: (sample) => this.sample(sample),
    });
    this.motion = globalThis.matchMedia('(prefers-reduced-motion: reduce)');
    try {
      this.motionPreference = localStorage.getItem(motionKey) === 'true';
    } catch {}
    this.motionChanged = () => this.setMotion();
    this.motion.addEventListener('change', this.motionChanged);
    this.el('live-reduce-motion').onchange = (event) => {
      this.motionPreference = event.target.checked;
      try {
        localStorage.setItem(motionKey, String(this.motionPreference));
      } catch {}
      this.setMotion();
    };
    this.visibilityChanged = () => this.meter.configure({ visible: !this.document.hidden });
    this.document.addEventListener('visibilitychange', this.visibilityChanged);
    this.setMotion();
    this.render();
  }

  setMotion() {
    this.reducedMotion = this.motion.matches || !!this.motionPreference;
    this.root.dataset.reducedMotion = String(this.reducedMotion);
    const control = this.el('live-reduce-motion');
    control.checked = this.reducedMotion;
    control.disabled = this.motion.matches;
    control.setAttribute(
      'aria-label',
      this.motion.matches ? 'Reduce motion (enabled by your system)' : 'Reduce motion',
    );
    if (this.reducedMotion) this.draw(0, []);
  }

  connection(connection, message) {
    this.state = updatePresence(this.state, { type: 'connection', state: connection, message });
    this.root.dataset.state = connection;
    if (connection === 'connecting') {
      this.rows = [];
      this.lastTime = -1;
      this.el('live-captions').replaceChildren();
      this.el('live-history').hidden = true;
      this.el('live-history').open = false;
      this.el('live-caption').textContent = 'A moment to connect. Then speak naturally.';
      this.el('live-caption-speaker').textContent = 'Your conversation is starting';
      this.state.page =
        this.el('run-mode').value === 'extension'
          ? 'Your attached Chrome tabs'
          : 'Separate browser window';
      this.meter.prepare();
    }
    if (connection === 'listening' && !this.rows.length) {
      this.el('live-caption').textContent = 'What would you like to do?';
      this.el('live-caption-speaker').textContent = 'Speak naturally · You can interrupt anytime';
    }
    if (['idle', 'error', 'ending'].includes(connection)) {
      this.meter.stop();
      this.el('live-caption-speaker').textContent = 'Microphone off';
      this.el('live-caption').textContent =
        connection === 'ending' ? 'Ending your conversation…' : 'Ready whenever you are.';
    } else
      this.meter.configure({
        enabled: connection === 'listening',
        visible: !this.document.hidden,
        muted: this.state.muted,
      });
    this.render();
  }

  event(event) {
    this.state = updatePresence(this.state, event);
    if (event.type === 'transcript') this.transcript(event);
    if (this.state.task === 'needs_information' && event.type === 'message' && event.feedback) {
      this.el('live-caption').textContent = clipCaption(event.message || '');
      this.el('live-caption-speaker').textContent = 'PANINI · Your answer is needed';
    }
    if (event.type === 'muted') this.meter.configure({ muted: event.muted });
    this.render();
  }

  transcript(event) {
    if (!['user', 'assistant'].includes(event.role) || typeof event.delta !== 'string') return;
    const captions = this.el('live-captions');
    const follow = captions.scrollHeight - captions.scrollTop - captions.clientHeight < 60;
    let row = this.rows.at(-1);
    if (!row || row.role !== event.role || event.start_ms - this.lastTime > 1800) {
      const element = this.document.createElement('p');
      element.className = event.role;
      const label = this.document.createElement('strong');
      label.textContent = event.role === 'user' ? 'You: ' : 'PANINI: ';
      const text = this.document.createTextNode('');
      element.append(label, text);
      row = { role: event.role, element, text, value: '' };
      this.rows.push(row);
      captions.append(element);
    }
    row.value = (row.value + event.delta).slice(-6000);
    row.text.textContent = row.value;
    this.lastTime = event.end_ms;
    while (this.rows.length > 60) this.rows.shift().element.remove();
    this.el('live-history').hidden = false;
    this.el('live-caption-count').textContent =
      `· ${this.rows.length} ${this.rows.length === 1 ? 'turn' : 'turns'}`;
    this.el('live-caption-speaker').textContent = event.role === 'user' ? 'You' : 'PANINI';
    this.el('live-caption').textContent = clipCaption(row.value);
    if (follow) captions.scrollTop = captions.scrollHeight;
  }

  sample({ speaking, energy, bands }) {
    if (speaking !== this.state.speaking) {
      this.state = updatePresence(this.state, { type: 'audio', speaking });
      this.renderActivity();
    }
    if (!this.reducedMotion) this.draw(energy, bands);
  }

  draw(energy, bands) {
    this.root.style.setProperty('--live-energy', String(energy));
    this.bars.forEach((bar, index) => {
      const band = bands[Math.abs(index - 21 - (index > 21 ? 1 : 0))] || 0;
      const distance = Math.abs(index - 21.5) / 22;
      const scale =
        0.045 + Math.min(0.955, (0.24 + band * 0.7) * Math.sqrt(energy) * (1 - distance * 0.5));
      bar.style.transform = `scaleY(${scale.toFixed(3)})`;
      bar.style.opacity = String(0.45 + Math.min(0.5, energy));
    });
  }

  renderActivity() {
    let [activity, label] = presenceActivity(this.state);
    if (['idle', 'error'].includes(this.state.connection) && this.wake.enabled) {
      activity = 'wake';
      label =
        {
          listening: 'Waiting for “Hey Panini”',
          waking: 'Starting PANINI',
          starting: 'Starting wake listening',
          restarting: 'Reconnecting wake listening',
        }[this.wake.state] || 'Wake listening paused';
    }
    this.root.dataset.activity = activity;
    // Avoid live-region mutations on every audio frame.
    if (this.el('live-activity').textContent !== label)
      this.el('live-activity').textContent = label;
  }

  text(id, value) {
    const node = this.el(id);
    if (node.textContent !== value) node.textContent = value;
  }

  wakeStatus(status) {
    this.wake = status;
    this.root.dataset.wake = status.state;
    this.el('live-wake').dataset.state = status.state;
    this.el('live-wake-enabled').checked = status.enabled;
    this.el('live-wake-enabled').disabled = !status.supported;
    this.text('live-wake-diagnostic-status', status.message);
    this.el('live-wake-language').value = status.language || 'en-US';
    this.text(
      'live-wake-hints',
      status.nameHints
        ? '“Hey Panini” name hint enabled.'
        : 'Name hints are used when your browser supports them.',
    );
    if (!status.enabled) this.text('live-wake-heard', 'Wake listening is off.');
    this.render();
  }

  wakeDetail(message) {
    if (this.el('live-wake-details').open) this.text('live-wake-heard', message);
  }

  wakeSummary() {
    const status = this.wake;
    if (status.state === 'paused' && !['idle', 'error'].includes(this.state.connection))
      return 'Resumes when your conversation ends.';
    return (
      {
        off: 'Off · Start a conversation with your voice.',
        listening: 'Listening for “Hey Panini”.',
        waking: 'Starting your conversation…',
        starting: 'Turning on wake listening…',
        restarting: 'Reconnecting wake listening…',
      }[status.state] || status.message
    );
  }

  render() {
    const state = this.state;
    const idle = ['idle', 'error'].includes(state.connection);
    const ending = state.connection === 'ending';
    const connected = state.connection === 'listening';
    this.root.dataset.taskState = state.task;
    this.root.dataset.notice = state.error ? 'error' : '';
    this.text('live-wake-status', this.wakeSummary());
    this.renderActivity();
    this.text(
      'live-connection',
      {
        idle: 'Ready to connect',
        error: 'Connection unavailable',
        connecting: 'Connecting',
        listening: 'Live conversation',
        reconnecting: 'Reconnecting',
        ending: 'Ending conversation',
      }[state.connection],
    );
    this.text(
      'live-status',
      state.error || state.playbackBlocked || !connected
        ? state.notice
        : state.muted
          ? 'Microphone muted · Browser work can continue.'
          : 'Mic on · You can interrupt anytime',
    );
    this.text('live-task-state', taskLabels[state.task] || 'Browser status');
    this.text('live-task', state.taskDetail);
    this.el('live-task').hidden = !!state.feedback && state.taskDetail === state.feedback;
    this.text('live-page', state.page || 'Choose your browser below');
    this.el('live-feedback').hidden = !state.feedback || state.task === 'needs_information';
    this.text(
      'live-feedback-title',
      state.task === 'needs_information' ? 'PANINI needs your input' : 'Browser feedback',
    );
    this.text('live-feedback-text', state.feedback);
    const focused = this.document.activeElement;
    this.el('live-start').hidden = !idle;
    this.el('live-start').disabled = !idle;
    this.el('live-end').hidden = idle;
    this.el('live-end').disabled = idle || ending;
    this.el('live-end').textContent = ending ? 'Ending…' : 'End conversation';
    this.el('live-mute').disabled = !connected;
    this.el('live-mute').textContent = state.muted ? 'Unmute mic' : 'Mute mic';
    this.el('live-mute').setAttribute('aria-pressed', String(state.muted));
    // Stop remains available for pending clarification, routing and reconnecting.
    this.el('live-stop').disabled =
      idle || ending || state.connection === 'connecting' || this.stopping;
    this.el('live-stop').textContent = this.stopping ? 'Stopping…' : 'Stop browser task';
    this.el('live-device').disabled = !idle;
    this.el('live-play').hidden = !state.playbackBlocked || idle || ending;
    if (focused === this.el('live-start') && !idle) this.el('live-end').focus();
    else if (focused === this.el('live-end') && idle) this.el('live-start').focus();
    if (this.renderedResults !== state.results) this.results(state.results);
    if (idle && this.wake.enabled) {
      this.text(
        'live-connection',
        this.wake.state === 'listening' ? 'Wake listening' : 'Wake phrase enabled',
      );
      this.text('live-status', this.wakeSummary());
      this.text(
        'live-caption-speaker',
        this.wake.state === 'listening' ? 'Default microphone · Wake listening on' : 'Wake phrase',
      );
      this.text(
        'live-caption',
        this.wake.state === 'listening'
          ? 'Say “Hey Panini” to begin.'
          : this.wake.state === 'waking'
            ? 'A moment to connect…'
            : 'Ready when your browser is available.',
      );
    } else if (idle) {
      this.text('live-caption-speaker', 'Microphone off');
      this.text('live-caption', 'What would you like to do?');
    }
  }

  results(items) {
    this.renderedResults = items;
    const list = this.el('live-results');
    list.replaceChildren();
    for (const [index, item] of items.entries()) {
      let url;
      try {
        const candidate = new URL(item.url);
        if (['https:', 'http:'].includes(candidate.protocol)) url = candidate.href;
      } catch {}
      const card = this.document.createElement('li');
      const title = this.document.createElement(url ? 'a' : 'strong');
      title.textContent = item.title;
      if (url) Object.assign(title, { href: url, target: '_blank', rel: 'noopener noreferrer' });
      const detail = this.document.createElement('p');
      detail.textContent = item.detail;
      card.append(title, detail);
      card.dataset.itemId = item.id;
      card.setAttribute('aria-label', `Result ${index + 1}: ${item.title}`);
      list.append(card);
    }
    this.el('live-results-section').hidden = !items.length;
    this.el('live-results-title').textContent =
      `${items.length} ${items.length === 1 ? 'result' : 'results'} from your browser`;
  }

  dispose() {
    this.el('live-wake-details').ontoggle = null;
    this.el('live-wake-settings-toggle').onclick = null;
    this.meter.stop();
    this.motion.removeEventListener('change', this.motionChanged);
    this.document.removeEventListener('visibilitychange', this.visibilityChanged);
  }
}
