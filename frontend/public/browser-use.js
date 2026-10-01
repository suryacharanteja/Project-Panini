const $ = (id) => document.getElementById(id);
const escapeHtml = (value) =>
  String(value ?? '').replace(
    /[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' })[c],
  );
let sttConfigured = false;
let recognition = null,
  recorder = null,
  recordingStream = null,
  recordingTimer = null;
let recording = false,
  transcribing = false,
  running = false,
  stopping = false;
let settling = false;
let captureVersion = 0;
let runController = null;
let speechController = null;
const commands = [];
let extensionState = null;
let extensionError = '';
let targetTimer = null;
let voiceActive = false;

function selectedRunPolicy() {
  const choice = $('run-policy')?.value || 'continuous';
  return ['25', '100'].includes(choice)
    ? {
        run_policy: 'bounded',
        max_steps: Number(choice),
        timeout_seconds: choice === '25' ? 300 : 900,
      }
    : { run_policy: 'continuous' };
}

function onRunPolicyChanged() {
  try {
    localStorage.setItem('jev-run-policy', $('run-policy').value);
  } catch {}
  $('run-help').textContent =
    selectedRunPolicy().run_policy === 'continuous'
      ? 'No step or time cutoff. Stops when done, blocked, disconnected, or when you press Stop.'
      : 'Stops at your chosen limit. Stop remains available throughout the run.';
  controls();
}

function selectedTarget() {
  const mode = $('run-mode').value;
  if (mode === 'chromium') return { mode };
  if (
    mode === 'extension' &&
    extensionState?.connected &&
    [2, 3].includes(extensionState.protocol) &&
    extensionState.compatible &&
    extensionState.attached_tab != null &&
    extensionState.target_id
  )
    return { mode, target_id: extensionState.target_id };
  return null;
}

function status(text) {
  $('status-text').textContent = text;
}
function debug(text) {
  $('stt-debug').textContent = text;
}
function targetUnavailableReason() {
  if ($('run-mode').value !== 'extension') return 'Choose the browser to control above.';
  if (!extensionState) return extensionError || 'Checking the extension connection…';
  if (!extensionState.connected)
    return 'JevRev Claw is not connected. Pair it once with the button above, then open its popup.';
  if (!extensionState.compatible)
    return 'Update JevRev Claw to 3.0 or later in chrome://extensions, then attach your website tab.';
  if (extensionState.attached_tab == null)
    return 'In JevRev Claw, choose a website tab and click Attach selected tab.';
  return 'The tab attachment is not ready. Reconnect the extension to this app and attach the tab again.';
}
function controls() {
  if ($('run-mode'))
    $('run-mode').disabled = voiceActive || running || recording || transcribing || settling;
  if ($('run-policy'))
    $('run-policy').disabled = voiceActive || running || recording || transcribing || settling;
  const ready = !!selectedTarget();
  const unavailable = voiceActive
    ? 'Live conversation is using this browser. Speak your next instruction, or end the conversation to type.'
    : settling
      ? 'Stopping the previous instruction. Waiting for the browser to become idle…'
      : running
        ? 'An instruction is running. Use Stop to cancel it before starting another.'
        : transcribing
          ? 'Transcribing your recording…'
          : ready
            ? ''
            : targetUnavailableReason();
  $('process-btn').disabled = !!unavailable;
  $('process-btn').title = unavailable;
  $('run-readiness').textContent =
    unavailable ||
    `Ready on ${location.host} · ${selectedRunPolicy().run_policy === 'continuous' ? 'Runs until complete or stopped' : 'Uses your selected run limit'}`;
  $('reset-btn').disabled = voiceActive || running || settling;
  $('stop-btn').disabled = settling || (!running && !recording && !transcribing);
  $('record-btn').textContent = recording ? 'Stop recording' : 'Record (Auto STT)';
  for (const id of ['record-btn', 'record-assembly-btn', 'record-webspeech-btn'])
    $(id).disabled = voiceActive || (!ready && !recording) || transcribing || settling;
  window.dispatchEvent?.(new Event('jev-browser-availability'));
}

async function checkSttStatus() {
  try {
    const res = await fetch('/api/stt/status');
    if (!res.ok) throw new Error('Voice service unavailable');
    const data = await res.json();
    sttConfigured = data.configured;
    $('stt-pill').className = sttConfigured ? 'stt-pill ok' : 'stt-pill warn';
    $('stt-label').textContent = sttConfigured
      ? 'AssemblyAI ready'
      : 'Web Speech available where supported';
    $('stt-hint').textContent = sttConfigured
      ? 'Auto records audio for AssemblyAI. Web Speech supports live commands.'
      : 'Use Web Speech or type a command.';
  } catch (error) {
    debug(error.message);
  }
}

function onModeChanged() {
  try {
    localStorage.setItem('jev-browser-mode', $('run-mode').value);
  } catch {}
  renderTarget();
  refreshExtensionState();
  fetchBrowserStatus();
}

function renderTarget() {
  const mode = $('run-mode').value;
  renderWorkspaceState(extensionState);
  $('target-help').textContent =
    mode === 'extension'
      ? extensionState?.workspace
        ? 'Uses the tabs you approved in JevRev Claw. The agent can switch between them while keeping your task and conversation.'
        : 'Uses only the tab you attach, including its signed-in session. For multiple tabs, choose Tab workspace in JevRev Claw.'
      : mode === 'chromium'
        ? 'Opens a visible, separate browser on the first instruction, then reuses it. Supports complex workflows; sign in inside that window if needed.'
        : 'Choose your attached Chrome tab or a separate browser window. Your choice is remembered.';
  $('process-btn').textContent =
    mode === 'extension'
      ? extensionState?.workspace
        ? 'Run in tab workspace'
        : 'Run in attached tab'
      : mode === 'chromium'
        ? 'Run in separate browser'
        : 'Choose a browser first';
  if (mode === 'extension') {
    $('screenshot').textContent = selectedTarget()
      ? 'Watch actions live in your attached Chrome tab. Keep it beside this dashboard.'
      : 'Open JevRev Claw, choose a website tab and click Attach selected tab.';
    $('page-url').textContent = extensionState?.tab_url || 'No website tab attached';
    if (!running && !settling)
      $('browser-mode').textContent = selectedTarget()
        ? 'Attached Chrome tab ready'
        : 'Waiting for an attached website tab';
  } else if (!running && $('screenshot').dataset.mode !== mode) {
    $('screenshot').textContent =
      'Browser snapshots appear here when available. Watch the separate browser for live actions.';
  }
  $('screenshot').dataset.mode = mode;
  controls();
}

function renderWorkspaceState(data) {
  const panel = $('workspace-state');
  if (!panel) return;
  const w = data?.workspace;
  panel.hidden = !w || $('run-mode').value !== 'extension';
  if (!w) {
    panel.innerHTML = '';
    return;
  }
  panel.innerHTML = `<p><strong>JevRev workspace · ${w.tabs.length} tabs</strong> <span>${w.allow_new_tabs ? 'New tabs allowed' : 'New tabs disabled'}</span></p><ul>${w.tabs.map((t) => `<li class="${t.id === w.selected_tab ? 'current' : ''}"><span>${t.id === w.selected_tab ? 'Current target · ' : ''}${escapeHtml(t.title || t.url)}</span><small>${escapeHtml(t.url)}</small></li>`).join('')}</ul><p class="workspace-note">Manage approved tabs in the Claw extension. Stop applies across this workspace.</p>`;
}

/** One-time code for pairing the extension with this PANINI runtime. */
async function pairExtension() {
  const output = $('pair-code');
  output.textContent = 'Creating a pairing code…';
  try {
    const response = await fetch('/api/extension/pairing-code', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: '{}',
    });
    if (!response.ok)
      throw new Error(
        response.status === 401
          ? 'Sign in to the PANINI console first.'
          : 'Could not create a pairing code.',
      );
    const { code, expires_in: expires } = await response.json();
    output.textContent = ` Enter ${code} in the JevRev Claw popup. It works once, for ${Math.round(expires / 60)} minutes.`;
  } catch (error) {
    output.textContent = ` ${error.message}`;
  }
}

async function refreshExtensionState() {
  const el = $('extension-state');
  if (!el) return;
  try {
    const res = await fetch('/api/browser/extension/status', { signal: AbortSignal.timeout(3000) });
    if (!res.ok) throw new Error('Extension status unavailable');
    const data = await res.json();
    renderWorkspaceState(data);
    // Freeze the displayed target while recording or executing queued commands.
    if (voiceActive || running || recording || transcribing || settling) return;
    extensionState = data;
    extensionError = '';
    if (!data.connected) {
      el.textContent =
        'JevRev Claw is not connected. Load panini-core/capabilities/browser/extension/ in chrome://extensions, pair it once with the code below, and keep Chrome open.';
    } else if (!data.compatible) {
      el.textContent =
        'Extension update needed. Reload JevRev Claw 3.0 or later in chrome://extensions, then attach the tab again.';
    } else if (data.attached_tab == null) {
      el.textContent =
        'Connected, but no website tab is attached. Open the extension, choose a Website tab and click Attach selected tab. Chrome settings pages cannot be controlled.';
    } else {
      el.textContent = `Attached: ${data.tab_title || `tab ${data.attached_tab}`} — ${data.tab_url || 'address unavailable'}`;
    }
  } catch (error) {
    if (voiceActive || running || recording || transcribing || settling) return;
    extensionState = null;
    extensionError =
      'Cannot reach the extension service. Check that this app is running, then refresh.';
    el.textContent = error.message;
  }
  renderTarget();
}

async function fetchBrowserStatus() {
  try {
    const mode = $('run-mode').value;
    const res = await fetch(`/api/browser/status?mode=${mode || 'chromium'}`, {
      signal: AbortSignal.timeout(3000),
    });
    if (!res.ok) throw new Error('Browser service unavailable');
    const data = await res.json();
    $('browser-mode').textContent = !mode
      ? 'Choose a browser above'
      : data.active
        ? 'Running an instruction'
        : (mode === 'extension' ? !!selectedTarget() : data.stats.browser_started)
          ? $('run-mode')?.value === 'extension'
            ? 'Attached Chrome tab ready'
            : 'Visible Chromium ready'
          : data.stats.last_error ||
            (mode === 'extension'
              ? 'Waiting for an attached Chrome tab'
              : 'Browser starts on first instruction');
    return data;
  } catch (error) {
    $('browser-mode').textContent = error.message;
  }
}

function stopRecording(discard = false) {
  if (discard) captureVersion++;
  clearTimeout(recordingTimer);
  if (recognition) {
    discard ? recognition.abort() : recognition.stop();
    recognition = null;
  }
  if (recorder && recorder.state !== 'inactive') {
    recorder.discard = discard;
    recorder.stop();
  }
  recordingStream?.getTracks().forEach((track) => track.stop());
  recordingStream = null;
  recording = false;
  controls();
}

async function toggleRecording(mode = 'auto') {
  if (recording) return stopRecording();
  if (transcribing) return;
  if (!selectedTarget()) return debug('Choose an available browser target first.');
  stopping = false;
  const engine = mode === 'auto' ? (sttConfigured ? 'assemblyai' : 'webspeech') : mode;
  if (engine === 'webspeech') return startWebSpeech();
  if (!sttConfigured)
    return debug('AssemblyAI is not configured. Choose Web Speech or type a command.');
  const version = ++captureVersion;
  try {
    if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder)
      throw new Error('Audio recording is unavailable in this browser');
    recording = true;
    controls();
    if (window.JevBrowser.prepareDictation && !(await window.JevBrowser.prepareDictation()))
      throw new Error('Wake microphone did not close. Reload the page before recording.');
    if (version !== captureVersion || stopping || !recording) return;
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    if (version !== captureVersion || stopping || !recording) {
      stream.getTracks().forEach((track) => track.stop());
      return;
    }
    recordingStream = stream;
    const mimeType = ['audio/webm;codecs=opus', 'audio/mp4', 'audio/webm'].find((type) =>
      MediaRecorder.isTypeSupported(type),
    );
    const current = new MediaRecorder(stream, mimeType ? { mimeType } : {});
    recorder = current;
    const chunks = [];
    let bytes = 0;
    current.ondataavailable = (event) => {
      if (version !== captureVersion) return;
      bytes += event.data.size;
      chunks.push(event.data);
      if (bytes > 10 * 1024 * 1024) {
        debug('Recording exceeded 10 MiB. Record a shorter command.');
        stopRecording(true);
      }
    };
    current.onerror = () => {
      if (version === captureVersion) {
        debug('Recording failed');
        stopRecording(true);
      }
    };
    current.onstop = async () => {
      stream.getTracks().forEach((track) => track.stop());
      if (version !== captureVersion) return;
      if (recordingStream === stream) recordingStream = null;
      if (recorder === current) recorder = null;
      clearTimeout(recordingTimer);
      recording = false;
      if (current.discard || stopping) {
        controls();
        return;
      }
      const blob = new Blob(chunks, { type: current.mimeType || 'audio/webm' });
      if (blob.size < 500) {
        debug('Recording is too short');
        controls();
        return;
      }
      transcribing = true;
      controls();
      debug('Transcribing audio…');
      try {
        const form = new FormData();
        form.append(
          'file',
          blob,
          current.mimeType.includes('mp4') ? 'command.m4a' : 'command.webm',
        );
        speechController = new AbortController();
        const res = await fetch('/api/stt/transcribe', {
          method: 'POST',
          body: form,
          signal: AbortSignal.any([speechController.signal, AbortSignal.timeout(95000)]),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || data.error || 'Transcription failed');
        if (!stopping && data.text?.trim()) {
          $('voice-input').value = data.text;
          enqueue(data.text);
          debug(`Transcribed in ${data.latency_ms}ms`);
        } else if (!stopping) debug('No speech detected. Try again.');
      } catch (error) {
        if (!stopping) debug(`${error.message}. You can choose Web Speech or type a command.`);
      } finally {
        transcribing = false;
        speechController = null;
        controls();
      }
    };
    current.start(500);
    recording = true;
    controls();
    $('stat-stt').textContent = 'AssemblyAI';
    debug('Recording. Click again to transcribe (maximum two minutes).');
    recordingTimer = setTimeout(() => stopRecording(), 120000);
  } catch (error) {
    if (version === captureVersion) {
      stopRecording(true);
      debug(error.message);
    }
  }
}

async function startWebSpeech() {
  if (!selectedTarget()) return debug('Choose an available browser target first.');
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRecognition)
    return debug('Web Speech is unavailable. Use AssemblyAI or type a command.');
  const version = ++captureVersion;
  recording = true;
  controls();
  if (window.JevBrowser.prepareDictation) {
    try {
      if (!(await window.JevBrowser.prepareDictation()))
        throw new Error('Wake microphone did not close. Reload the page before dictating.');
    } catch (error) {
      if (version === captureVersion) {
        recording = false;
        controls();
        debug(error.message);
      }
      return;
    }
    if (version !== captureVersion || stopping || !recording) return;
  }
  const current = new SpeechRecognition();
  recognition = current;
  current.lang = 'en-US';
  current.continuous = true;
  current.interimResults = true;
  current.onresult = (event) => {
    if (version !== captureVersion || stopping) return;
    let interim = '';
    for (let i = event.resultIndex; i < event.results.length; i++) {
      const text = event.results[i][0].transcript.trim();
      if (event.results[i].isFinal) {
        $('voice-input').value = text;
        enqueue(text);
      } else interim += text;
    }
    if (interim) debug(`Hearing: ${interim}`);
  };
  current.onerror = (event) => {
    if (version === captureVersion) debug(`Speech recognition: ${event.error}`);
  };
  current.onend = () => {
    if (version !== captureVersion) return;
    if (recognition === current) recognition = null;
    recording = false;
    controls();
  };
  try {
    current.start();
    recording = true;
    controls();
    $('stat-stt').textContent = 'Web Speech';
    debug('Listening. Completed utterances run in order.');
  } catch (error) {
    recognition = null;
    recording = false;
    controls();
    debug(error.message);
  }
}

function enqueue(instruction) {
  if (voiceActive) return;
  if (stopping || !instruction.trim()) return;
  const target = selectedTarget();
  if (!target) return status('Choose an available browser target first.');
  if (commands.length >= 5) return debug('Command queue is full. Wait for the current commands.');
  commands.push({ instruction: instruction.trim(), ...target, ...selectedRunPolicy() });
  if (!running) runNext();
}

// Narrow integration boundary for PANINI; microphone and conversation state live in their own module.
let voiceMetrics = { steps: 0, latency: 0, cost: 0, confidence: 0 };
window.JevBrowser = {
  target: selectedTarget,
  canStartVoice: () => !running && !recording && !transcribing && !settling && !!selectedTarget(),
  lockVoice(active) {
    voiceActive = active;
    if (active) {
      voiceMetrics = { steps: 0, latency: 0, cost: 0, confidence: 0 };
      resetMetrics();
      $('stat-stt').textContent = 'OpenAI Live';
      $('decision-actions').replaceChildren();
    }
    controls();
    if (!active) refreshExtensionState();
  },
  voiceEvent(event) {
    if (event.screenshot_b64) {
      const img = document.createElement('img');
      img.src = `data:image/jpeg;base64,${event.screenshot_b64}`;
      img.alt = 'Current browser page';
      img.style.cssText = 'max-width:100%;max-height:300px';
      $('screenshot').replaceChildren(img);
    }
    if (event.event === 'step') {
      voiceMetrics.steps++;
      voiceMetrics.latency += event.jev_latency_ms || 0;
      voiceMetrics.cost += event.cost || 0;
      voiceMetrics.confidence += event.confidence || 0;
      $('stat-actions').textContent = voiceMetrics.steps;
      $('stat-latency').textContent = `${Math.round(voiceMetrics.latency / voiceMetrics.steps)}ms`;
      $('stat-cost').textContent = `$${voiceMetrics.cost.toFixed(6)}`;
      $('stat-confidence').textContent =
        `${((voiceMetrics.confidence / voiceMetrics.steps) * 100).toFixed(1)}%`;
    }
    if (event.url) $('page-url').textContent = event.url;
    const row = document.createElement('div');
    row.className = 'action-row';
    row.textContent = event.message || event.error || event.event;
    const panel = $('decision-actions');
    panel.append(row);
    while (panel.childElementCount > 100) panel.firstElementChild.remove();
    status(row.textContent);
  },
};

async function runNext() {
  const command = commands.shift();
  if (!command || stopping) return;
  running = true;
  controls();
  status(
    command.mode === 'extension'
      ? 'Running in your attached Chrome tab…'
      : 'Running in the separate browser…',
  );
  $('decision-actions').replaceChildren();
  resetMetrics();
  runController = new AbortController();
  let terminal = false,
    steps = 0,
    latency = 0,
    cost = 0,
    confidence = 0;
  const handle = (event) => {
    const type = event.event;
    if (event.screenshot_b64) {
      const img = document.createElement('img');
      img.src = `data:image/jpeg;base64,${event.screenshot_b64}`;
      img.alt = 'Current browser page';
      img.style.cssText = 'max-width:100%;max-height:300px';
      $('screenshot').replaceChildren(img);
    }
    if (event.url) $('page-url').textContent = event.url;
    const row = document.createElement('div');
    row.className = 'action-row';
    row.textContent =
      type === 'step'
        ? `Step ${event.step}: ${event.action} — ${event.message || event.rationale}`
        : event.error || event.message || type;
    $('decision-actions').append(row);
    if (type === 'step') {
      steps++;
      latency += event.jev_latency_ms || 0;
      cost += event.cost || 0;
      confidence += event.confidence || 0;
      const options = document.createElement('div');
      options.innerHTML = (event.options || [])
        .map(
          (option) =>
            `<div style="font-size:.8em;padding:4px">${(option.probability * 100).toFixed(1)}% ${escapeHtml(option.action)}: ${escapeHtml(option.rationale)}</div>`,
        )
        .join('');
      $('decision-actions').append(options);
      $('stat-actions').textContent = steps;
      $('stat-latency').textContent = `${Math.round(latency / steps)}ms`;
      $('stat-cost').textContent = `$${cost.toFixed(6)}`;
      $('stat-confidence').textContent = `${((confidence / steps) * 100).toFixed(1)}%`;
    }
    // Keep the live panel bounded even for long-running tasks.
    const panel = $('decision-actions');
    while (panel.childElementCount > 200) panel.firstElementChild.remove();
    if (['complete', 'incomplete', 'error', 'timeout', 'cancelled'].includes(type)) {
      terminal = true;
      status(`${type}: ${event.error || event.message || ''}`);
      if (type !== 'complete') commands.length = 0;
    }
  };
  try {
    const res = await fetch('/api/browser/run-stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(command),
      signal: runController.signal,
    });
    if (!res.ok) {
      const data = await res.json();
      throw new Error(data.detail || data.error || `HTTP ${res.status}`);
    }
    if (!res.body) throw new Error('No event stream returned');
    const reader = res.body.getReader(),
      decoder = new TextDecoder();
    let buffer = '';
    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop();
        for (const line of lines) if (line.trim()) handle(JSON.parse(line));
      }
      buffer += decoder.decode();
      if (buffer.trim()) handle(JSON.parse(buffer));
      if (!terminal) throw new Error('Connection ended before the instruction completed');
    } finally {
      reader.releaseLock();
    }
  } catch (error) {
    commands.length = 0;
    status(error.name === 'AbortError' ? 'Instruction stopped' : `Error: ${error.message}`);
  } finally {
    running = false;
    runController = null;
    controls();
    fetchBrowserStatus();
    if (commands.length && !stopping) runNext();
    else refreshExtensionState();
  }
}

async function stopAll() {
  if (settling) return;
  stopping = true;
  settling = true;
  commands.length = 0;
  stopRecording(true);
  status('Stopping…');
  speechController?.abort();
  try {
    const res = await fetch('/api/browser/stop', {
      method: 'POST',
      signal: AbortSignal.timeout(5000),
    });
    if (!res.ok) throw new Error('Could not stop the browser agent');
    // Let the cancellation event reach the client; abort also tears down an unreachable stream.
    runController?.abort();
    for (let attempt = 0; attempt < 40; attempt++) {
      const data = await fetchBrowserStatus();
      if (!data) throw new Error('Could not confirm that the browser stopped');
      if (!data.active) {
        status('Instruction stopped');
        return;
      }
      status('Stopping browser agent…');
      await new Promise((resolve) => setTimeout(resolve, 250));
    }
    throw new Error(
      'Browser cleanup is still running. Check Browser Status before the next instruction.',
    );
  } catch (error) {
    status(error.message);
  } finally {
    settling = false;
    controls();
  }
}

async function resetAgent() {
  try {
    const res = await fetch('/api/browser/reset', { method: 'POST' });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Could not clear history');
    $('decision-actions').replaceChildren();
    $('voice-input').value = '';
    status('History cleared');
    resetMetrics();
  } catch (error) {
    status(error.message);
  }
}

function resetMetrics() {
  $('stat-actions').textContent = '0';
  $('stat-cost').textContent = '$0.00';
  $('stat-latency').textContent = $('stat-confidence').textContent = '—';
}

document.addEventListener('DOMContentLoaded', () => {
  try {
    const saved = localStorage.getItem('jev-browser-mode');
    if (['chromium', 'extension'].includes(saved)) $('run-mode').value = saved;
    const savedPolicy = localStorage.getItem('jev-run-policy');
    if (['continuous', '25', '100'].includes(savedPolicy)) $('run-policy').value = savedPolicy;
  } catch {}
  onRunPolicyChanged();
  $('record-btn').onclick = () => toggleRecording();
  $('record-assembly-btn').onclick = () => toggleRecording('assemblyai');
  $('record-webspeech-btn').onclick = () => toggleRecording('webspeech');
  $('process-btn').onclick = () => {
    stopping = false;
    enqueue($('voice-input').value);
  };
  $('stop-btn').onclick = stopAll;
  $('reset-btn').onclick = resetAgent;
  renderTarget();
  refreshExtensionState();
  targetTimer = setInterval(() => {
    refreshExtensionState();
  }, 5000);
  checkSttStatus();
  fetchBrowserStatus();
});
window.addEventListener('pagehide', () => {
  clearInterval(targetTimer);
  stopping = true;
  stopRecording(true);
  runController?.abort();
  speechController?.abort();
});
