import React, { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import './style.css';
import { ExecutionPanel } from './ExecutionPanel';
import { ToolCard, type ToolCall } from '../../../shared-ui';
import '../../../shared-ui/tokens.css';
import { ComputerUsePanel, PanelBoundary } from './ComputerUsePanel';
import { ShellLive } from './ShellLive';
import { Avatar, type AvatarState, levelMeter } from './avatar/Avatar';
import { avatarStateFor } from './avatar/state';
import { Backdrop, Capabilities, Engine, Lexicon, StageTitle } from './Stage';
import { presentation } from '../../../phone/lib/persona.mjs';

declare global {
  interface Window {
    panini: {
      invoke(method: string, value?: unknown): Promise<any>;
      subscribe(callback: (event: any) => void): () => void;
      heartbeat(): void;
    };
  }
}
const api = window.panini;
function CredentialSetup() {
  const [openai, setOpenai] = useState(''),
    [gateway, setGateway] = useState(''),
    [error, setError] = useState('');
  return (
    <details className="credentials">
      <summary>Provider credentials</summary>
      <form
        onSubmit={async (event) => {
          event.preventDefault();
          try {
            await api.invoke('credentials', { openai, gateway });
            setOpenai('');
            setGateway('');
          } catch (e: any) {
            setError(e.message);
          }
        }}
      >
        <label>
          OpenAI API key
          <input
            type="password"
            autoComplete="off"
            required
            value={openai}
            onChange={(e) => setOpenai(e.target.value)}
          />
        </label>
        <label>
          AI Gateway API key
          <input
            type="password"
            autoComplete="off"
            required
            value={gateway}
            onChange={(e) => setGateway(e.target.value)}
          />
        </label>
        <small>Encrypted for your Windows account. PANINI restarts after saving.</small>
        <button type="submit">Save and restart</button>
        {error && <span role="alert">{error}</span>}
      </form>
    </details>
  );
}
function App() {
  const [execution, setExecution] = useState<any>(null);
  const [computerEvents, setComputerEvents] = useState<any[]>([]);
  const [tools, setTools] = useState<ToolCall[]>([]);
  const [executionMode, setExecutionMode] = useState('compact');
  const [presence, setPresence] = useState('avatar');
  const [background, setBackground] = useState<any>(null);
  const executionTask = useRef('');
  const [outcome, setOutcome] = useState('');
  const [activeCap, setActiveCap] = useState('');
  const [delegationOverlay, setDelegationOverlay] = useState(false);
  const [view, setView] = useState<'stage' | 'compact'>('stage');
  const [log, setLog] = useState<string[]>(() => {
    const t = new Date().toLocaleTimeString([], { hour12: false });
    return [`${t} > INIT_SEQUENCE`, `${t} > GATEWAY :3200 · RUNTIME HOST`];
  });
  const anchors = useRef<Record<string, HTMLElement | null>>({});
  const note = (line: string) =>
    setLog((all) => [
      ...all.slice(-160),
      `${new Date().toLocaleTimeString([], { hour12: false })} > ${line}`,
    ]);
  const micMeter = useRef<ReturnType<typeof levelMeter> | null>(null),
    voiceMeter = useRef<ReturnType<typeof levelMeter> | null>(null);
  const [steps, setSteps] = useState<any[]>([]);
  const [choices, setChoices] = useState<any[]>([]);
  const [rated, setRated] = useState('');
  const [open, setOpen] = useState(true),
    [ready, setReady] = useState(false),
    [snapshot, setSnapshot] = useState<any>(null);
  const [capability, setCapability] = useState(''),
    [targets, setTargets] = useState<any[]>([]),
    [caps, setCaps] = useState<any[]>([]),
    [browsers, setBrowsers] = useState<any[]>([]);
  const [input, setInput] = useState(''),
    [message, setMessage] = useState('Preparing your conversation…'),
    [phase, setPhase] = useState('connecting');
  const [persona, setPersona] = useState(presentation(null));
  const [voicePersona, setVoicePersona] = useState('panini');
  const [voicePersonas, setVoicePersonas] = useState<ReturnType<typeof presentation>[]>([]);
  const [personaMessages, setPersonaMessages] = useState<
    { name: string; color: string; text: string }[]
  >([]);
  const [caption, setCaption] = useState(''),
    [results, setResults] = useState<any[]>([]),
    [error, setError] = useState('');
  const [mic, setMic] = useState(false),
    [muted, setMuted] = useState(false),
    [connectingMic, setConnectingMic] = useState(false);
  const [microphones, setMicrophones] = useState<MediaDeviceInfo[]>([]),
    [microphone, setMicrophone] = useState('');
  const [pin, setPin] = useState(false),
    [reduced, setReduced] = useState(false),
    [sharing, setSharing] = useState(false);
  const audio = useRef<HTMLAudioElement>(null),
    peer = useRef<RTCPeerConnection | null>(null),
    local = useRef<MediaStream | null>(null),
    generation = useRef(0),
    disposed = useRef(false);
  const terminal = [
    'completed',
    'blocked',
    'failed',
    'stopped',
    'ready',
    'connecting',
    'suspended',
    'needs_input',
  ].includes(phase);
  const expand = (value: boolean) => {
    setOpen(value);
    void api.invoke('window', { action: 'expand', value });
  };
  const call = async (method: string, payload?: unknown) => {
    setError('');
    try {
      return await api.invoke(method, payload);
    } catch (e: any) {
      setError(e.message.replace(/^Error invoking remote method '[^']+': Error: /, ''));
      return null;
    }
  };
  const apply = (value: any) => {
    if (!value) return;
    setSnapshot(value);
    setReady(value.state === 'ready');
    setPhase(value.task?.state || value.state);
    setMessage(value.task?.message || 'What would you like to do?');
    setResults(value.results || []);
    executionTask.current = value.task?.task_id || '';
    setExecution(value.task?.execution || null);
    setComputerEvents(value.task?.trace || []);
    setOutcome(value.task?.outcome || '');
    setActiveCap(value.task?.capability || '');
    setSteps(value.task?.steps || []);
    setChoices(value.task?.choices || []);
  };
  const releaseAudio = () => {
    micMeter.current?.close();
    voiceMeter.current?.close();
    micMeter.current = voiceMeter.current = null;
    generation.current++;
    local.current?.getTracks().forEach((t) => t.stop());
    local.current = null;
    peer.current?.close();
    peer.current = null;
    if (audio.current) audio.current.srcObject = null;
    setMic(false);
    setMuted(false);
    setConnectingMic(false);
  };
  const closeMic = async () => {
    const current = generation.current;
    releaseAudio();
    return await call('media-close', { generation: current });
  };
  const startMic = async (personaId = voicePersona, reconnect = false) => {
    if (!reconnect && (mic || connectingMic)) return;
    setConnectingMic(true);
    const current = ++generation.current;
    try {
      const media = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          ...(microphone ? { deviceId: { exact: microphone } } : {}),
        },
        video: false,
      });
      if (generation.current !== current || disposed.current) {
        media.getTracks().forEach((t) => t.stop());
        return;
      }
      local.current = media;
      micMeter.current?.close();
      micMeter.current = levelMeter(media);
      setMicrophones(
        (await navigator.mediaDevices.enumerateDevices()).filter(
          (device) => device.kind === 'audioinput',
        ),
      );
      if (generation.current !== current || disposed.current) return;
      media.getAudioTracks().forEach((track) => {
        track.onended = () => {
          if (generation.current === current) void closeMic();
        };
      });
      const connection = new RTCPeerConnection();
      peer.current = connection;
      media.getTracks().forEach((track) => connection.addTrack(track, media));
      connection.ontrack = (event) => {
        voiceMeter.current?.close();
        voiceMeter.current = levelMeter(event.streams[0] ?? null);
        if (audio.current) {
          audio.current.srcObject = event.streams[0];
          void audio.current
            .play()
            .catch(() => setError('Click the microphone to enable audio playback.'));
        }
      };
      connection.onconnectionstatechange = () => {
        if (connection.connectionState === 'connected') {
          setMic(true);
          setConnectingMic(false);
        } else if (['failed', 'disconnected'].includes(connection.connectionState)) void closeMic();
      };
      connection.createDataChannel('oai-events');
      await connection.setLocalDescription(await connection.createOffer());
      if (connection.iceGatheringState !== 'complete')
        await new Promise<void>((resolve) => {
          const finish = () => {
            clearTimeout(timer);
            connection.removeEventListener('icegatheringstatechange', changed);
            resolve();
          };
          const changed = () => {
            if (connection.iceGatheringState === 'complete') finish();
          };
          const timer = setTimeout(finish, 2500);
          connection.addEventListener('icegatheringstatechange', changed);
        });
      if (generation.current !== current) return;
      const answer = await call('media', {
        generation: current,
        persona_id: personaId,
        sdp: connection.localDescription?.sdp,
      });
      if (!answer || generation.current !== current) {
        if (generation.current === current) releaseAudio();
        return;
      }
      await connection.setRemoteDescription({ type: 'answer', sdp: answer.sdp });
      setTimeout(() => {
        if (generation.current === current && connection.connectionState !== 'connected') {
          void closeMic();
          setError('Audio could not connect. Check the network and retry.');
        }
      }, 18000);
    } catch (e: any) {
      if (generation.current === current) {
        releaseAudio();
        void api.invoke('media-close', { generation: current }).catch(() => {});
        setError(e.message || 'Microphone connection failed.');
      }
    }
  };
  const mute = (value: boolean) => {
    local.current?.getAudioTracks().forEach((t) => {
      t.enabled = !value;
    });
    setMuted(value);
  };
  useEffect(() => {
    const heartbeat = setInterval(() => api.heartbeat(), 1000);
    api.heartbeat();
    const unsubscribe = api.subscribe((event) => {
      const data = event.data || {};
      if (event.type === 'snapshot') {
        apply(data);
        if (data?.state) note(`SESSION · ${String(data.state).toUpperCase()}`);
      } else if (event.type === 'code.activity') {
        executionTask.current = event.task_id;
        setExecution(data);
      } else if (event.type === 'computer.activity') {
        setComputerEvents((all) =>
          [...all.filter((item) => item.sequence !== data.sequence), data].slice(-48),
        );
      } else if (event.type.startsWith('task.')) {
        const state = event.type.slice(5);
        if (event.task_id && event.task_id !== executionTask.current) {
          executionTask.current = event.task_id;
          setExecution(null);
          setOutcome('');
          setSteps([]);
          setChoices([]);
          setTools([]);
          setPersona(presentation(null));
          setPersonaMessages([]);
        }
        if (data.persona) {
          const p = presentation(data.persona);
          setPersona(p);
          if (data.agent_event === 'persona.message')
            setPersonaMessages((all) =>
              [
                ...all,
                { name: p.name, color: p.color, text: String(data.message || '').slice(0, 16000) },
              ].slice(-20),
            );
        }
        // V2-07: each agent tool call arrives as a card (redacted arguments and result).
        if (data.agent_event === 'tool.started' && data.tool?.id)
          setTools((all) =>
            [
              ...all.filter((t) => t.id !== data.tool.id),
              {
                id: String(data.tool.id),
                name: String(data.tool.name || ''),
                args: data.tool.args,
              },
            ].slice(-12),
          );
        if (data.agent_event === 'tool.finished' && data.tool?.id)
          setTools((all) =>
            all.map((t) => (t.id === data.tool.id ? { ...t, output: data.tool.output ?? '' } : t)),
          );
        if (data.execution) setExecution(data.execution);
        if (data.outcome) setOutcome(data.outcome);
        if (data.steps) setSteps(data.steps);
        if (data.choices) setChoices(data.choices);
        if (event.capability) setActiveCap(event.capability);
        note(
          `${state.toUpperCase()}${event.capability ? ` · ${String(event.capability).toUpperCase()}` : ''}${data.message ? ` · ${String(data.message).slice(0, 90)}` : ''}`,
        );
        setPhase(state);
        setMessage(data.message || 'Working…');
        if (data.results) setResults(data.results);
      } else if (event.type.startsWith('caption.'))
        setCaption((previous) => (previous + (data.delta || data.text || '')).slice(-800));
      else if (event.type === 'controller.lost' || event.type === 'runtime.error') {
        setReady(false);
        setError(data.message);
        setPhase('suspended');
        releaseAudio();
      } else if (event.type === 'harness.status') {
        setBackground(data);
      } else if (event.type === 'shell.view') {
        setView(data.view === 'compact' ? 'compact' : 'stage');
      } else if (event.type === 'shell.presence') {
        setPresence(data.presence);
      } else if (event.type === 'execution.viewer_error') {
        setError(data.message);
      } else if (event.type === 'media.connected' && data.persona) {
        setPersona(presentation(data.persona));
      } else if (event.type === 'media.closed' || event.type === 'session.closed') {
        if (
          event.type === 'media.closed' &&
          data.generation != null &&
          data.generation < generation.current
        )
          return;
        releaseAudio();
        if (event.type === 'session.closed') {
          setReady(false);
          setPhase('suspended');
          setMessage('Conversation ended.');
        }
      } else if (event.type === 'desktop.focus_required') {
        setMessage(data.message);
      } else if (event.type === 'target.selected')
        void api.invoke('status').then((value) => apply(value.snapshot));
    });
    void api.invoke('status').then((value) => {
      apply(value.snapshot);
      setPin(!!value.preferences.alwaysOnTop);
      setDelegationOverlay(!!value.preferences.delegationOverlay);
      setView(value.preferences.view === 'compact' ? 'compact' : 'stage');
      note(value.connected ? 'GATEWAY_ACTIVE · CONTROLLER LEASE HELD' : 'GATEWAY · CONNECTING');
      setReduced(!!value.preferences.reducedMotion);
      setMicrophone(value.preferences.microphone || '');
      setExecutionMode(value.preferences.executionMode || 'compact');
      setPresence(value.preferences.presence || 'avatar');
    });
    return () => {
      disposed.current = true;
      clearInterval(heartbeat);
      unsubscribe();
      local.current?.getTracks().forEach((t) => t.stop());
      peer.current?.close();
    };
  }, []);
  useEffect(() => {
    const refreshDevices = async () => {
      try {
        const devices = await navigator.mediaDevices.enumerateDevices();
        if (!disposed.current)
          setMicrophones(devices.filter((device) => device.kind === 'audioinput'));
      } catch {}
    };
    void refreshDevices();
    navigator.mediaDevices.addEventListener('devicechange', refreshDevices);
    return () => navigator.mediaDevices.removeEventListener('devicechange', refreshDevices);
  }, []);
  useEffect(() => {
    void api.invoke('window', {
      action: 'microphone-state',
      value: mic && !muted ? 'on' : mic ? 'muted' : 'off',
    });
  }, [mic, muted]);
  useEffect(() => {
    if (ready)
      void api
        .invoke('capabilities')
        .then((value) => setCaps(value.capabilities))
        .catch(() => {});
  }, [ready]);
  useEffect(() => {
    if (ready && ['browser', 'desktop', 'code'].includes(capability))
      void call('targets', { capability }).then((value) => setTargets(value?.targets || []));
    else setTargets([]);
  }, [capability, ready]);
  // Which browser PANINI uses: your Chrome through JevRev Claw, or the separate browser.
  const loadBrowsers = () =>
    void call('targets', { capability: 'browser' }).then((value) =>
      setBrowsers(value?.targets || []),
    );
  useEffect(() => {
    if (ready) loadBrowsers();
  }, [ready]);
  useEffect(() => {
    if (ready)
      void call('voice-personas').then((value) =>
        setVoicePersonas((value?.personas || []).map(presentation)),
      );
  }, [ready]);
  const chosenBrowser = snapshot?.selected_targets?.browser;
  useEffect(() => {
    if (phase !== 'completed' || execution) return;
    const timer = setTimeout(() => {
      if (document.activeElement?.tagName !== 'TEXTAREA') expand(false);
    }, 7000);
    return () => clearTimeout(timer);
  }, [phase, execution]);
  const changeExecutionMode = (value: string) => {
    setExecutionMode(value);
    void call('window', { action: 'execution-mode', value });
  };
  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!input.trim()) return;
    const receipt = await call('turn', { text: input, capability });
    if (receipt) {
      setInput('');
      setPhase('preparing');
      setMessage('Preparing your instruction.');
    }
  };
  const selected = snapshot?.selected_targets?.[capability];
  // The full-screen delegation moment fires once per capability hand-off (opt-in).
  const delegated = useRef('');
  useEffect(() => {
    const key = `${executionTask.current}:${activeCap}`;
    if (
      !delegationOverlay ||
      terminal ||
      !activeCap ||
      phase === 'preparing' ||
      delegated.current === key
    )
      return;
    delegated.current = key;
    void api.invoke('window', { action: 'delegate', value: activeCap }).catch(() => {});
  }, [activeCap, phase, terminal, delegationOverlay]);
  // The avatar mirrors exactly what PANINI is doing (Visibility of System Status).
  const avatarState: AvatarState = avatarStateFor({ phase, ready, mic, muted });
  // Minimized and PANINI needs the person: glow and notify once per request.
  const needsYou = avatarState === 'approval' || phase === 'needs_input' || phase === 'blocked';
  const called = useRef(false);
  useEffect(() => {
    if (needsYou && !open && !called.current) {
      called.current = true;
      void call('window', { action: 'attention', message });
    }
    if (!needsYou) called.current = false;
  }, [needsYou, open]);
  const staged = view === 'stage' && open;
  const switchView = (next: 'stage' | 'compact') => {
    setView(next);
    void call('window', { action: 'view', value: next });
  };
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.ctrlKey && event.key === ',') {
        event.preventDefault();
        void call('admin', { page: '/' });
      } else if (event.key === 'F11' && open) {
        event.preventDefault();
        switchView(staged ? 'compact' : 'stage');
      } else if (event.ctrlKey && event.key.toLowerCase() === 'h') {
        event.preventDefault();
        expand(false);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, staged]);
  useEffect(() => {
    if (!staged) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && document.activeElement?.tagName !== 'TEXTAREA')
        switchView('compact');
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [staged]);
  return (
    <main
      className={`${open ? 'card' : 'capsule'} ${staged ? 'stage' : ''} ${reduced ? 'reduce-motion' : ''}`}
    >
      {staged && (
        <>
          <Backdrop />
          <Engine
            state={avatarState}
            personaColor={persona.color}
            personaName={persona.name}
            capability={activeCap}
            micLevel={() => (muted ? 0 : (micMeter.current?.read() ?? 0))}
            voiceLevel={() => voiceMeter.current?.read() ?? 0}
            reducedMotion={reduced}
            anchors={anchors}
            listening={mic}
            onActivate={() => void (mic ? closeMic() : startMic())}
          />
          <Lexicon log={log} caps={caps} />
          <Capabilities
            caps={caps}
            active={activeCap}
            state={avatarState}
            selected={capability}
            onSelect={setCapability}
            registerAnchor={(id, element) => {
              // React 19 treats a ref callback's return value as its cleanup: return nothing.
              anchors.current[id] = element;
            }}
          />
        </>
      )}
      {!open && (
        <Bubble
          state={avatarState}
          persona={persona}
          capability={activeCap}
          message={message}
          micLevel={() => (muted ? 0 : (micMeter.current?.read() ?? 0))}
          voiceLevel={() => voiceMeter.current?.read() ?? 0}
          reducedMotion={reduced}
          onOpen={() => expand(true)}
        />
      )}
      <header hidden={!open}>
        <button
          className={`presence ${mic && !muted ? 'listening' : ''} ${!terminal ? 'working' : ''} state-${avatarState}`}
          onClick={() => expand(!open)}
          aria-label={open ? 'Collapse PANINI' : 'Expand PANINI'}
          title={`PANINI · ${avatarState}`}
        >
          <Avatar
            state={avatarState}
            personaColor={persona.color}
            capability={activeCap}
            micLevel={() => (muted ? 0 : (micMeter.current?.read() ?? 0))}
            voiceLevel={() => voiceMeter.current?.read() ?? 0}
            reducedMotion={reduced}
          />
        </button>
        <div className="brand">
          <strong title={persona.name}>{persona.name}</strong>
          <span>
            {mic
              ? muted
                ? 'Microphone muted'
                : 'Listening'
              : !terminal
                ? 'Working with you'
                : ready
                  ? 'Here when you need me'
                  : 'Connecting'}
          </span>
        </div>
        {staged && <StageTitle state={avatarState} />}
        <nav className="window-controls labelled" aria-label="Window">
          {open && (
            <button
              className="control"
              onClick={() => void call('admin', { page: '/' })}
              aria-label="Open PANINI Admin (Ctrl+,)"
              title="PANINI Admin — pairing, keys, models, usage and settings (Ctrl+,)"
            >
              <ControlIcon name="admin" />
              <span>Admin</span>
            </button>
          )}
          {open && (
            <button
              className="control"
              onClick={() => switchView(staged ? 'compact' : 'stage')}
              aria-label={staged ? 'Compact view (F11 or Esc)' : 'Full screen (F11)'}
              title={staged ? 'Compact view (F11 or Esc)' : 'Full screen (F11)'}
            >
              <ControlIcon name={staged ? 'compact' : 'full'} />
              <span>{staged ? 'Compact' : 'Full screen'}</span>
            </button>
          )}
          <button
            className="control"
            onClick={() => expand(false)}
            aria-label="Minimize to the PANINI bubble (Ctrl+H)"
            title="Minimize to the PANINI bubble: it stays on screen and shows what PANINI is doing (Ctrl+H)"
          >
            <ControlIcon name="bubble" />
            <span>Minimize</span>
          </button>
        </nav>
      </header>
      {open && (
        <>
          <section className="conversation">
            <div className="eyebrow">YOUR CONVERSATION</div>
            <h1>
              {phase === 'completed'
                ? 'All set.'
                : phase === 'needs_input'
                  ? 'One detail to continue.'
                  : phase === 'blocked' && outcome === 'partial'
                    ? 'Ready for your review.'
                    : phase === 'blocked'
                      ? 'A little help needed.'
                      : phase === 'awaiting_confirmation'
                        ? 'Your confirmation.'
                        : !terminal
                          ? 'On it.'
                          : 'Let’s get something done.'}
            </h1>
            <p className="message" role="status" aria-live="polite">
              {message}
            </p>
            {personaMessages.length > 0 && (
              <details className="persona-timeline">
                <summary>Team conversation</summary>
                {personaMessages.map((item, index) => (
                  <div key={index}>
                    <span className="persona-chip" data-color={item.color}>
                      {item.name}
                    </span>
                    <p>{item.text}</p>
                  </div>
                ))}
              </details>
            )}
            {background &&
              (background.running || background.queued > 0 || background.reviews > 0) && (
                <p className="background-work" role="status" aria-live="polite">
                  <span
                    className={background.running ? 'pulse' : 'pulse idle'}
                    aria-hidden="true"
                  />
                  {background.running
                    ? `Working in the background${background.current ? `: ${background.current.title}` : ''}`
                    : 'Background tasks waiting'}
                  {background.queued > 0 && ` · ${background.queued} queued`}
                  {background.reviews > 0 && ` · ${background.reviews} to review`}
                </p>
              )}
            {['recording', 'starting'].includes(background?.recording?.state) && (
              <p className="background-work" role="status">
                <span className="pulse" aria-hidden="true" /> Recording your demonstration
                <button onClick={() => void call('teachStop')}>Stop recording</button>
              </p>
            )}
            {(background?.mcp ?? []).map((item: { client: string; tool: string; hand: string }) => (
              <p className="background-work" role="status" key={`${item.client}:${item.tool}`}>
                <span className="pulse" aria-hidden="true" />
                {item.client} is using{' '}
                {item.hand === 'desktop' ? 'your desktop' : `PANINI: ${item.tool}`}
              </p>
            ))}
            {caption && (
              <div className="captions" aria-label="Live captions">
                {caption}
              </div>
            )}
            {error && (
              <p className="error" role="alert">
                {error}
              </p>
            )}
            {steps.length > 0 && (
              <ol className="workflow" aria-label="Task progress">
                {steps.map((step, index) => (
                  <li key={index} aria-current={step.state === 'running' ? 'step' : undefined}>
                    <div>
                      <strong>
                        {index + 1}. {step.capability}
                      </strong>
                      <span>{step.state.replaceAll('_', ' ')}</span>
                    </div>
                    <p>{step.instruction}</p>
                    {step.target_label && <small>{step.target_label}</small>}
                  </li>
                ))}
              </ol>
            )}
            {phase === 'needs_input' && choices.length > 0 && (
              <div className="target-choices" role="group" aria-label="Choose where to continue">
                {choices.map((choice) => (
                  <button
                    key={choice.choice_id}
                    disabled={!ready}
                    onClick={async () => {
                      const result = await call('turn', {
                        text: choice.label,
                        resume_choice_id: choice.choice_id,
                        expected_task_id: executionTask.current,
                      });
                      if (result) {
                        setChoices([]);
                        setPhase('preparing');
                      }
                    }}
                  >
                    {choice.label}
                  </button>
                ))}
              </div>
            )}
            {execution && (
              <ExecutionPanel
                activity={execution}
                phase={phase}
                outcome={outcome}
                mode={executionMode}
                setMode={changeExecutionMode}
                connected={ready}
                stop={() => void call('stop')}
                openTerminal={() => void call('execution-viewer')}
              />
            )}
            {tools.length > 0 && (
              <PanelBoundary>
                <div className="tool-cards" aria-label="Tool calls">
                  {tools.map((call) => (
                    <ToolCard key={call.id} call={call} />
                  ))}
                </div>
              </PanelBoundary>
            )}
            <PanelBoundary>
              <ComputerUsePanel events={computerEvents} phase={phase} />
            </PanelBoundary>
            <PanelBoundary>
              <ShellLive />
            </PanelBoundary>
            {phase === 'completed' && executionTask.current && (
              <div className="rate" role="group" aria-label="Rate this result">
                {rated === executionTask.current ? (
                  <span>Thanks — PANINI learns from this.</span>
                ) : (
                  <>
                    <span>How did PANINI do?</span>
                    {(['up', 'down'] as const).map((rating) => (
                      <button
                        key={rating}
                        aria-label={rating === 'up' ? 'Good result' : 'Not right'}
                        title={
                          rating === 'up'
                            ? 'Good result: PANINI keeps doing this'
                            : 'Not right: PANINI learns what to change (tell it why in your next message)'
                        }
                        onClick={async () => {
                          await call('feedback', { task_id: executionTask.current, rating });
                          setRated(executionTask.current);
                        }}
                      >
                        {rating === 'up' ? '👍' : '👎'}
                      </button>
                    ))}
                  </>
                )}
              </div>
            )}
            {!!results.length && (
              <div className="results">
                {results.map((result) => (
                  <button
                    key={result.result_id}
                    disabled={!result.url}
                    onClick={() => void call('open-result', { result_id: result.result_id })}
                  >
                    <strong>{result.title}</strong>
                    <span>{result.detail}</span>
                    {result.url && <small>Open source ↗</small>}
                  </button>
                ))}
              </div>
            )}
          </section>
          <section className="controls">
            <div className="selectors">
              <label>
                Browser
                <select
                  aria-label="Browser"
                  disabled={!terminal}
                  value={chosenBrowser?.choice_id || ''}
                  onFocus={loadBrowsers}
                  onChange={async (e) => {
                    if (e.target.value)
                      await call('select', { capability: 'browser', choice_id: e.target.value });
                  }}
                >
                  <option value="">Your Chrome when connected</option>
                  {browsers.map((target) => (
                    <option key={target.choice_id} value={target.choice_id}>
                      {target.choice_id === 'managed-chromium'
                        ? 'Separate browser'
                        : `Your Chrome · ${target.label}`}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Work with
                <select
                  aria-label="Capability"
                  value={capability}
                  disabled={!terminal}
                  onChange={(e) => setCapability(e.target.value)}
                >
                  <option value="">Auto</option>
                  {['browser', 'search', 'desktop', 'code'].map((name) => (
                    <option
                      key={name}
                      value={name}
                      disabled={caps.find((c) => c.capability === name)?.state === 'unavailable'}
                    >
                      {name[0].toUpperCase() + name.slice(1)}
                    </option>
                  ))}
                </select>
              </label>
              {['browser', 'desktop', 'code'].includes(capability) && (
                <label>
                  Target
                  <select
                    aria-label="Target"
                    disabled={!terminal}
                    value={selected ? '__selected' : ''}
                    onChange={async (e) => {
                      if (e.target.value) {
                        await call('select', { capability, choice_id: e.target.value });
                      }
                    }}
                  >
                    <option value="">Auto-select a clear match</option>
                    {selected && <option value="__selected">{selected.label}</option>}
                    {targets.map((target) => (
                      <option key={target.choice_id} value={target.choice_id}>
                        {target.label}
                      </option>
                    ))}
                  </select>
                </label>
              )}
              {['browser', 'desktop', 'code'].includes(capability) && (
                <button
                  className="icon refresh"
                  title="Refresh targets"
                  onClick={() =>
                    void call('targets', { capability }).then((value) =>
                      setTargets(value?.targets || []),
                    )
                  }
                >
                  ↻
                </button>
              )}
            </div>
            <form onSubmit={submit}>
              <label className="sr-only" htmlFor="instruction">
                Your instruction
              </label>
              <textarea
                id="instruction"
                value={input}
                onChange={(e) => setInput(e.target.value)}
                placeholder="Tell PANINI what you need…"
                maxLength={5000}
                rows={2}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    void submit(e);
                  }
                }}
              />
              <div className="composer-footer">
                <span>Enter to send · Shift + Enter for a new line</span>
                <button
                  className="send"
                  disabled={!ready || !terminal || !input.trim()}
                  aria-label="Send instruction"
                >
                  ↑
                </button>
              </div>
            </form>
            <div className="actions">
              <button
                className={mic ? 'mic active' : 'mic'}
                disabled={!ready || connectingMic}
                onClick={() => void (mic ? closeMic() : startMic())}
              >
                {connectingMic
                  ? 'Connecting…'
                  : mic
                    ? muted
                      ? 'Microphone muted · End call'
                      : 'Listening · End call'
                    : '◉ Talk to PANINI'}
              </button>
              {mic && <button onClick={() => mute(!muted)}>{muted ? 'Unmute' : 'Mute'}</button>}
              <button className="stop" disabled={!ready} onClick={() => void call('stop')}>
                ■ Stop
              </button>
            </div>
            {mic && (
              <button
                className="ptt"
                onPointerDown={(e) => {
                  e.currentTarget.setPointerCapture(e.pointerId);
                  mute(false);
                }}
                onPointerUp={() => mute(true)}
                onPointerCancel={() => mute(true)}
                onKeyDown={(e) => {
                  if (e.key === ' ') mute(false);
                }}
                onKeyUp={(e) => {
                  if (e.key === ' ') mute(true);
                }}
              >
                Hold to talk
              </button>
            )}
            {!ready && (
              <button className="reconnect" onClick={() => void call('reconnect').then(apply)}>
                Reconnect conversation
              </button>
            )}
          </section>
          <footer>
            <button onClick={() => void call('dashboard', { shareCaptions: sharing })}>
              Open activity dashboard ↗
            </button>
            <details>
              <summary aria-label="Conversation settings">•••</summary>
              <div className="settings">
                <details className="capability-health">
                  <summary>Capability readiness</summary>
                  <p>
                    Ready means configured locally. A task also checks provider access and its
                    target.
                  </p>
                  {caps.map((item) => (
                    <div key={item.capability}>
                      <strong>
                        {item.capability[0].toUpperCase() + item.capability.slice(1)} ·{' '}
                        {item.state === 'ready' ? 'Ready' : 'Needs setup'}
                      </strong>
                      {item.reason && <p>{item.reason}</p>}
                    </div>
                  ))}
                  <button
                    onClick={() =>
                      void call('capabilities').then((value) => {
                        if (value) setCaps(value.capabilities);
                      })
                    }
                  >
                    Refresh readiness
                  </button>
                </details>
                <label>
                  When PANINI is working
                  <select
                    aria-label="Presence"
                    value={presence}
                    onChange={(event) => {
                      setPresence(event.target.value);
                      void call('window', { action: 'presence', value: event.target.value });
                    }}
                  >
                    <option value="avatar">Keep the avatar on screen</option>
                    <option value="background">Background mode: tray only, show when needed</option>
                  </select>
                </label>
                <label>
                  Execution visibility
                  <select
                    aria-label="Execution visibility"
                    value={executionMode}
                    onChange={(event) => changeExecutionMode(event.target.value)}
                  >
                    <option value="compact">Compact activity</option>
                    <option value="expanded">Expanded output</option>
                    <option value="external">Open PowerShell viewer automatically</option>
                  </select>
                </label>
                <label>
                  <input
                    type="checkbox"
                    checked={delegationOverlay}
                    onChange={(e) => {
                      setDelegationOverlay(e.target.checked);
                      void call('window', {
                        action: 'delegation-overlay',
                        value: e.target.checked,
                      });
                    }}
                  />
                  Show delegation across the screen
                </label>
                <label>
                  <input
                    type="checkbox"
                    checked={pin}
                    onChange={(e) => {
                      setPin(e.target.checked);
                      void call('window', { action: 'pin', value: e.target.checked });
                    }}
                  />
                  Always on top
                </label>
                <label>
                  <input
                    type="checkbox"
                    checked={reduced}
                    onChange={(e) => {
                      setReduced(e.target.checked);
                      void call('window', { action: 'motion', value: e.target.checked });
                    }}
                  />
                  Reduced motion
                </label>
                <label>
                  <input
                    type="checkbox"
                    checked={sharing}
                    onChange={(e) => setSharing(e.target.checked)}
                  />
                  Share captions with new viewers
                </label>
                <button onClick={() => void call('pair')}>Pair browser extension</button>
                <CredentialSetup />
                {voicePersonas.length > 0 && (
                  <label className="microphone-picker">
                    Voice specialist
                    <select
                      aria-label="Voice specialist"
                      value={voicePersona}
                      disabled={connectingMic}
                      onChange={async (event) => {
                        const id = event.target.value;
                        setVoicePersona(id);
                        if (mic) {
                          const closed = await closeMic();
                          if (closed !== null) await startMic(id, true);
                        }
                      }}
                    >
                      {voicePersonas.map((p) => (
                        <option key={p.id} value={p.id}>
                          {p.name}
                        </option>
                      ))}
                    </select>
                    <small>
                      During a call, switching stops current work and reconnects audio in this
                      conversation.
                    </small>
                  </label>
                )}
                <label className="microphone-picker">
                  Microphone
                  <select
                    value={microphone}
                    disabled={mic || connectingMic}
                    onChange={(event) => {
                      setMicrophone(event.target.value);
                      void call('window', { action: 'microphone', value: event.target.value });
                    }}
                  >
                    <option value="">System microphone</option>
                    {microphones
                      .filter((device) => device.deviceId !== 'default')
                      .map((device, index) => (
                        <option key={device.deviceId} value={device.deviceId}>
                          {device.label || `Microphone ${index + 1}`}
                        </option>
                      ))}
                  </select>
                </label>
                <button
                  onClick={() => {
                    releaseAudio();
                    void call('end');
                  }}
                >
                  End conversation
                </button>
                <small>
                  Ctrl + Shift + Space: open
                  <br />
                  Ctrl + Shift + Escape: stop
                </small>
              </div>
            </details>
          </footer>
        </>
      )}
      <audio ref={audio} autoPlay />
    </main>
  );
}
createRoot(document.getElementById('root')!).render(<App />);

/** Line-art window controls in the brand style (1.5px strokes, currentColor). */
function ControlIcon({ name }: { name: 'admin' | 'full' | 'compact' | 'tray' | 'bubble' }) {
  const common = {
    fill: 'none',
    stroke: 'currentColor',
    strokeWidth: 1.5,
    strokeLinecap: 'round' as const,
    strokeLinejoin: 'round' as const,
  };
  return (
    <svg viewBox="0 0 20 20" width="16" height="16" aria-hidden="true" {...common}>
      {name === 'admin' && (
        <>
          <circle cx="10" cy="10" r="2.6" />
          <path d="M10 2.5v2.2M10 15.3v2.2M2.5 10h2.2M15.3 10h2.2M4.7 4.7l1.6 1.6M13.7 13.7l1.6 1.6M4.7 15.3l1.6-1.6M13.7 6.3l1.6-1.6" />
        </>
      )}
      {name === 'full' && <path d="M3 7.5V3h4.5M17 7.5V3h-4.5M3 12.5V17h4.5M17 12.5V17h-4.5" />}
      {name === 'compact' && <path d="M7.5 3v4.5H3M12.5 3v4.5H17M7.5 17v-4.5H3M12.5 17v-4.5H17" />}
      {name === 'bubble' && (
        <>
          <circle cx="10" cy="9" r="5.5" />
          <path d="M5 17.5h10" />
        </>
      )}
      {name === 'tray' && (
        <>
          <path d="M10 3v8M6.5 7.5 10 11l3.5-3.5" />
          <path d="M3 12.5v3A1.5 1.5 0 0 0 4.5 17h11a1.5 1.5 0 0 0 1.5-1.5v-3" />
        </>
      )}
    </svg>
  );
}

const BUBBLE_STATUS: Record<AvatarState, string> = {
  offline: 'Connecting',
  idle: 'Ready',
  listening: 'Listening',
  thinking: 'Thinking',
  working: 'Working',
  approval: 'Needs you',
  success: 'Done',
  blocked: 'Needs a look',
  muted: 'Muted',
};

/** Minimized PANINI: a round, draggable bubble (about 5 cm) with the living avatar and a
    one-line status. Click to open; drag to move; it glows amber when PANINI needs you. */
function Bubble({
  state,
  capability,
  message,
  persona,
  micLevel,
  voiceLevel,
  reducedMotion,
  onOpen,
}: {
  state: AvatarState;
  capability: string;
  message: string;
  persona: { name: string; color: string };
  micLevel: () => number;
  voiceLevel: () => number;
  reducedMotion: boolean;
  onOpen: () => void;
}) {
  const drag = useRef<{ x: number; y: number; moved: boolean } | null>(null);
  const status =
    state === 'working' && capability
      ? `Working · ${capability[0].toUpperCase()}${capability.slice(1)}`
      : BUBBLE_STATUS[state];
  return (
    <div
      className={`bubble state-${state}`}
      role="button"
      tabIndex={0}
      aria-label={`${persona.name}: ${status}. ${message}. Press Enter to open.`}
      title={`${status}: ${message}\nClick to open, drag to move`}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') onOpen();
      }}
      onPointerDown={(e) => {
        e.currentTarget.setPointerCapture(e.pointerId);
        drag.current = { x: e.screenX, y: e.screenY, moved: false };
      }}
      onPointerMove={(e) => {
        const d = drag.current;
        if (!d) return;
        const dx = e.screenX - d.x,
          dy = e.screenY - d.y;
        if (!d.moved && Math.hypot(dx, dy) < 4) return;
        d.moved = true;
        d.x = e.screenX;
        d.y = e.screenY;
        void api.invoke('window', { action: 'move', dx, dy });
      }}
      onPointerUp={() => {
        const d = drag.current;
        drag.current = null;
        if (d?.moved) void api.invoke('window', { action: 'moved' });
        else onOpen();
      }}
    >
      <div className="bubble-avatar">
        <Avatar
          state={state}
          personaColor={persona.color}
          capability={capability}
          micLevel={micLevel}
          voiceLevel={voiceLevel}
          reducedMotion={reducedMotion}
        />
      </div>
      <div className="bubble-status">
        <strong>{status}</strong>
        <span>{message}</span>
      </div>
    </div>
  );
}
