import {
  app,
  BrowserWindow,
  ipcMain,
  Menu,
  Notification,
  Tray,
  nativeImage,
  globalShortcut,
  screen,
  session,
  protocol,
  net,
  dialog,
  shell,
  safeStorage,
  clipboard,
  powerMonitor,
} from 'electron';
import { spawn, type ChildProcess } from 'node:child_process';
import { createHash, randomBytes, randomUUID } from 'node:crypto';
import { createReadStream, existsSync, readFileSync, writeFileSync } from 'node:fs';
import { join, resolve, sep } from 'node:path';
import { pathToFileURL } from 'node:url';
import { PaniniClient } from './api';
import { ExecutionViewer } from './execution-viewer';
import { mintTicket } from '../../../../../frontend/server/console-auth.mjs';
import { DEFAULT_PORTS, readConfig } from '../../../../../frontend/server/config.mjs';

protocol.registerSchemesAsPrivileged([
  { scheme: 'panini', privileges: { standard: true, secure: true, supportFetchAPI: true } },
]);
app.setName('PANINI');
// Honour Electron's explicit profile directory for isolated installations and developer qualification.
const profileDirectory = app.commandLine.getSwitchValue('user-data-dir');
if (profileDirectory) app.setPath('userData', resolve(profileDirectory));
// Windows groups taskbar buttons by this ID; without it PANINI shows as "Electron".
if (process.platform === 'win32') app.setAppUserModelId('ai.ndimensions.panini');
const locked = app.requestSingleInstanceLock();
if (!locked) app.quit();
let win: BrowserWindow,
  tray: Tray,
  client: PaniniClient,
  quitting = false,
  runtimeFailed = false,
  connected = false,
  expanded = true;
let liveness = 0,
  lastHeartbeat = 0,
  renew: NodeJS.Timeout | undefined;
const owned: ChildProcess[] = [];
const stream = new AbortController();
const confirmations = new Set<string>();
const root = app.isPackaged
  ? join(process.resourcesPath, 'runtime')
  : resolve(__dirname, '../../../../..');
// Resolved from FRONTEND_PORT/BACKEND_PORT once .env is loaded; one runtime serves both.
let base = `http://127.0.0.1:${DEFAULT_PORTS[1]}`;
let dashboard = `http://127.0.0.1:${DEFAULT_PORTS[0]}`;
const instance = randomUUID();
const bootstrap = randomBytes(32).toString('base64url');
const desktopSecret = randomBytes(32).toString('base64url');
// Signs one-time web-console sign-in links; the runtime verifies them.
const consoleSecret = randomBytes(32).toString('base64url');
let hideExplained = false; // The first hide shows where PANINI went.
function consoleLink(next = '/') {
  return `${dashboard}/auth/login?ticket=${mintTicket(consoleSecret)}&next=${encodeURIComponent(next)}`;
}
let releaseId = 'development';
function withoutCredentials(env: NodeJS.ProcessEnv) {
  return Object.fromEntries(
    Object.entries(env).filter(([key]) => !/(key|secret|token|password|credential)/i.test(key)),
  );
}
async function verifyRuntime() {
  if (!app.isPackaged) return;
  const raw = readFileSync(join(root, 'release.json'));
  const manifest = JSON.parse(raw.toString('utf8'));
  if (manifest.product !== 'PANINI' || !Array.isArray(manifest.files) || !manifest.files.length)
    throw new Error('PANINI runtime manifest is invalid. Reinstall PANINI.');
  const listed = new Set<string>();
  for (const file of manifest.files) {
    const path = resolve(root, file.path);
    if (
      !path.startsWith(resolve(root) + sep) ||
      listed.has(path) ||
      !/^[a-f0-9]{64}$/.test(file.sha256)
    )
      throw new Error('PANINI runtime manifest is invalid. Reinstall PANINI.');
    listed.add(path);
    const hash = createHash('sha256');
    for await (const chunk of createReadStream(path)) hash.update(chunk);
    if (hash.digest('hex') !== file.sha256)
      throw new Error('PANINI runtime files have changed. Reinstall PANINI before continuing.');
  }
  for (const required of [
    'node/node.exe',
    'backend/Panini.Backend.exe',
    'scripts/panini-runtime.mjs',
    'desktop/host/Panini.RuntimeHost.exe',
    'desktop/broker/Panini.Desktop.Broker.exe',
    'desktop/worker/Panini.Desktop.Worker.exe',
    'desktop/assistant/PANINI Desktop Readiness.exe',
  ]) {
    if (!listed.has(resolve(root, required)))
      throw new Error('PANINI runtime is incomplete. Reinstall PANINI.');
  }
  releaseId = `${manifest.version}-${createHash('sha256').update(raw).digest('hex').slice(0, 16)}`;
}
let preferences: {
  x?: number;
  y?: number;
  alwaysOnTop?: boolean;
  reducedMotion?: boolean;
  microphone?: string;
  executionMode?: 'compact' | 'expanded' | 'external';
  // avatar: the avatar stays on screen. background: PANINI works from the tray and
  // appears only when it needs the person (a confirmation, a question) or is called.
  presence?: 'avatar' | 'background';
  // Opt-in: a brief full-screen sutra thread when PANINI delegates (off by default).
  delegationOverlay?: boolean;
  // stage: the full-screen Algorithmic Avatar (default). compact: the floating card.
  view?: 'stage' | 'compact';
  // Where the minimized bubble was last dragged to.
  bubble?: { x: number; y: number };
} = {};
// Minimized PANINI is a round bubble about 5 cm across (190 DIP at standard scaling): large
// enough for the living avatar and a one-line status, small enough to stay out of the way.
const BUBBLE = 190;
const staged = () => preferences.view !== 'compact';
let overlay: BrowserWindow | null = null;
function showDelegation(capability: string) {
  if (!preferences.delegationOverlay || !win || win.isDestroyed() || !win.isVisible()) return;
  if (!['search', 'browser', 'desktop', 'code', 'agent'].includes(capability)) return;
  const bounds = win.getBounds();
  const display = screen.getDisplayMatching(bounds);
  const area = display.bounds;
  // The thread starts at the avatar, in the overlay's coordinates.
  const x = bounds.x - area.x + 70;
  const y = bounds.y - area.y + 70;
  overlay?.destroy();
  overlay = new BrowserWindow({
    ...area,
    frame: false,
    transparent: true,
    resizable: false,
    movable: false,
    focusable: false,
    skipTaskbar: true,
    hasShadow: false,
    alwaysOnTop: true,
    show: false,
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true,
      devTools: false,
    },
  });
  overlay.setIgnoreMouseEvents(true); // Click-through: never blocks the person's work.
  overlay.setAlwaysOnTop(true, 'screen-saver');
  const current = overlay;
  current.on('closed', () => {
    if (overlay === current) overlay = null;
  });
  void current
    .loadURL(`panini://app/overlay.html?capability=${encodeURIComponent(capability)}&x=${x}&y=${y}`)
    .then(() => current.showInactive())
    .catch(() => current.destroy());
  setTimeout(() => {
    if (!current.isDestroyed()) current.destroy();
  }, 2500);
}
let harness: {
  running: boolean;
  current?: { title: string } | null;
  queued: number;
  reviews: number;
  notices: number;
} | null = null;
const executionViewer = new ExecutionViewer(
  () => join(app.getPath('userData'), 'cli-activity.json'),
  (message) => {
    if (win && !win.isDestroyed())
      win.webContents.send('panini:event', { type: 'execution.viewer_error', data: { message } });
  },
  () => join(__dirname, '../renderer/brand/panini-cli.ico'),
);
const prefFile = () => join(app.getPath('userData'), 'window.json');
function stopOwnedRuntime() {
  connected = false;
  clearInterval(renew);
  stream.abort();
  // Closing the supervisor pipes terminates only our job-owned process trees.
  for (const child of owned) child.stdin?.end();
}
function failRuntime(message: string) {
  if (quitting || runtimeFailed) return;
  console.error(`[PANINI runtime] ${message}`);
  runtimeFailed = true;
  stopOwnedRuntime();
  emit({ type: 'runtime.error', data: { message } });
}
function emit(value: any) {
  executionViewer.update(value, preferences.executionMode === 'external');
  if (win && !win.isDestroyed()) win.webContents.send('panini:event', value);
}
function show(focus = false) {
  if (win?.isDestroyed()) return;
  win.showInactive();
  if (focus) win.focus();
}
function trayMenu() {
  if (!tray) return;
  const background = preferences.presence === 'background';
  const working = harness?.running
    ? `Working: ${harness.current?.title?.slice(0, 60) || 'a task'}`
    : 'Idle';
  tray.setContextMenu(
    Menu.buildFromTemplate([
      { label: `PANINI — ${working}`, enabled: false },
      ...(harness && (harness.queued || harness.reviews || harness.notices)
        ? [
            {
              label: `${harness.notices} new · ${harness.queued} queued · ${harness.reviews} to review`,
              click: () => void shell.openExternal(consoleLink('/inbox')).catch(() => {}),
            },
          ]
        : []),
      { type: 'separator' as const },
      { label: 'Open PANINI', click: () => show(true) },
      {
        label: 'Open PANINI Admin inbox',
        click: () => void shell.openExternal(consoleLink('/inbox')).catch(() => {}),
      },
      {
        label: 'Open PANINI Admin',
        click: () => void shell.openExternal(consoleLink()).catch(() => {}),
      },
      {
        label: 'Avatar on',
        type: 'radio' as const,
        checked: !background,
        click: () => setPresence('avatar'),
      },
      {
        label: 'Background mode (tray only)',
        type: 'radio' as const,
        checked: background,
        click: () => setPresence('background'),
      },
      {
        label: 'Stop current task',
        click: () => {
          void client?.scoped('/stop', { command_id: randomUUID() }).catch(() => {});
        },
      },
      { type: 'separator' as const },
      { label: 'Quit PANINI', click: () => app.quit() },
    ]),
  );
  tray.setToolTip(`PANINI — ${working}${background ? ' (background mode)' : ''}`);
}
function setPresence(value: 'avatar' | 'background') {
  preferences.presence = value;
  save();
  if (value === 'avatar') show(true);
  else win?.hide();
  trayMenu();
  emit({ type: 'shell.presence', data: { presence: value } });
  void signalPresence();
}
let pcLocked = false;
let pcSuspended = false;
let continuityPolling = false;
let pcSignalBusy = false;
let pcSignalAgain = false;
const continuityNotes = new Map<string, Notification>();
let continuityLive: BrowserWindow | null = null;
async function openContinuityLive() {
  if (continuityLive && !continuityLive.isDestroyed()) {
    continuityLive.show();
    return;
  }
  continuityLive = new BrowserWindow({
    width: 1100,
    height: 850,
    title: 'PANINI · Live view',
    webPreferences: { nodeIntegration: false, contextIsolation: true, sandbox: true },
  });
  continuityLive.on('closed', () => {
    continuityLive = null;
  });
  await continuityLive.loadURL(consoleLink('/build/live'));
}
async function signalPresence() {
  if (!connected || !client) return;
  if (pcSignalBusy) {
    pcSignalAgain = true;
    return;
  }
  pcSignalBusy = true;
  try {
    do {
      pcSignalAgain = false;
      await client
        .call('/harness/presence/pc', {
          idle_s: Math.min(604800, powerMonitor.getSystemIdleTime()),
          locked: pcLocked,
          suspended: pcSuspended,
          avatar_visible: Boolean(win && !win.isDestroyed() && win.isVisible()),
        })
        .catch(() => {});
    } while (pcSignalAgain && connected);
  } finally {
    pcSignalBusy = false;
  }
}
async function pollContinuity() {
  if (!connected || !client || continuityPolling) return;
  continuityPolling = true;
  try {
    const { commands } = await client.call('/harness/presence/commands');
    for (const command of commands) {
      try {
        const data = command.data || {};
        if (command.kind === 'retract') {
          continuityNotes.get(data.id)?.close();
          continuityNotes.delete(data.id);
          emit({ type: 'continuity.retract', data });
        } else if (command.kind === 'handoff') {
          if (pcLocked || pcSuspended) continue;
          const restored = await client.call('/harness/presence/handoff/accept', {
            seq: command.seq,
            session_id: client.sessionId,
          });
          show(true);
          emit({ type: 'continuity.handoff', data: restored });
          await openContinuityLive();
        } else if (command.kind === 'notification') {
          if (!Notification.isSupported()) continue;
          const note = new Notification({
            title: 'PANINI',
            body: String(data.title || 'Check your Inbox').slice(0, 200),
          });
          const previous = continuityNotes.get(data.id);
          previous?.close();
          continuityNotes.set(data.id, note);
          note.on('close', () => {
            if (continuityNotes.get(data.id) === note) continuityNotes.delete(data.id);
          });
          note.on('click', () => void shell.openExternal(consoleLink('/inbox')).catch(() => {}));
          note.show();
          emit({ type: 'continuity.notification', data });
        } else continue;
        await client.call('/harness/presence/commands/ack', { seq: command.seq });
      } catch {
        /* A busy handoff must not prevent later notification retractions. */
      }
    }
  } catch {
  } finally {
    continuityPolling = false;
  }
}
async function pollHarness() {
  if (!connected || !client) return;
  try {
    const next = await client.call('/harness/status');
    // The first poll only sets the baseline; later increases are news.
    const newNotices = harness !== null && next.notices > (harness.notices ?? 0);
    const newReviews = harness !== null && next.reviews > (harness.reviews ?? 0);
    const changed = JSON.stringify(next) !== JSON.stringify(harness);
    harness = next;
    if (changed) {
      trayMenu();
      emit({ type: 'harness.status', data: next });
    }
    // In background mode, notifications are how the person knows PANINI is working.
    if (!next.presence?.enabled && Notification.isSupported() && (newNotices || newReviews)) {
      const note = new Notification({
        title: 'PANINI',
        body: newReviews
          ? 'A background task needs your decision.'
          : 'New in your PANINI inbox: a result, a watch alert or a suggestion.',
      });
      note.on('click', () => void shell.openExternal(consoleLink('/inbox')).catch(() => {}));
      note.show();
    }
  } catch {}
}
function save() {
  if (win && !win.isDestroyed()) {
    // The stage fills the screen; only the compact card's position is remembered.
    const { x, y } =
      staged() && expanded ? { x: preferences.x, y: preferences.y } : win.getBounds();
    preferences = { ...preferences, x, y };
    writeFileSync(prefFile(), JSON.stringify(preferences));
  }
}
function resize(open: boolean) {
  expanded = open;
  if (open && staged() && win && !win.isDestroyed()) {
    // The Algorithmic Avatar stage: the whole work area, taskbar still visible.
    win.setBounds(screen.getDisplayMatching(win.getBounds()).workArea);
    return;
  }
  const area = screen.getDisplayMatching(win.getBounds()).workArea;
  // A taskbar button while open, so Alt+Tab and the taskbar find PANINI; the bubble floats
  // on top without one.
  win.setSkipTaskbar(!open);
  win.setAlwaysOnTop(open ? Boolean(preferences.alwaysOnTop) : true, open ? 'normal' : 'floating');
  if (!open) {
    const saved = preferences.bubble;
    const x = saved ? saved.x : area.x + area.width - BUBBLE - 24;
    const y = saved ? saved.y : area.y + area.height - BUBBLE - 24;
    win.setBounds({
      x: Math.max(area.x, Math.min(x, area.x + area.width - BUBBLE)),
      y: Math.max(area.y, Math.min(y, area.y + area.height - BUBBLE)),
      width: BUBBLE,
      height: BUBBLE,
    });
    return;
  }
  const detailView = preferences.executionMode === 'expanded';
  const width = open ? Math.min(detailView ? 680 : 430, area.width - 16) : 236,
    height = open ? Math.min(detailView ? 850 : 690, area.height - 32) : 72;
  const pos = win.getBounds();
  const left = pos.x + pos.width / 2 < area.x + area.width / 2;
  const x = open
    ? left
      ? pos.x
      : pos.x + pos.width - width
    : left
      ? area.x + 8
      : area.x + area.width - width - 8;
  win.setBounds({
    x: Math.max(area.x, Math.min(x, area.x + area.width - width)),
    y: Math.max(area.y, Math.min(pos.y, area.y + area.height - height)),
    width,
    height,
  });
}
function launch(executable: string, args: string[], env: NodeJS.ProcessEnv) {
  const host = app.isPackaged
    ? join(root, 'desktop/host/Panini.RuntimeHost.exe')
    : join(
        root,
        'panini-core/capabilities/desktop/Panini.RuntimeHost/bin/Release/net10.0-windows/Panini.RuntimeHost.exe',
      );
  if (!existsSync(host) || !existsSync(executable))
    throw new Error('PANINI runtime is missing. Run npm run panini:build.');
  const child = spawn(host, [executable, ...args], {
    cwd: root,
    windowsHide: true,
    stdio: ['pipe', 'pipe', 'pipe'],
    env,
  });
  owned.push(child);
  child.stdin?.on('error', () => {});
  child.stdout?.resume();
  child.stderr?.on('data', (chunk) => process.stderr.write(`[${executable}] ${chunk}`));
  child.on('error', (error) =>
    failRuntime(`A PANINI companion could not start (${executable}): ${error.message}`),
  );
  child.on('exit', (code, signal) => {
    failRuntime(
      `A PANINI companion stopped (${executable}; exit ${code ?? signal}). Quit and reopen PANINI.`,
    );
  });
}
async function startRuntime() {
  await verifyRuntime();
  if (!app.isPackaged && existsSync(join(root, '.env'))) process.loadEnvFile(join(root, '.env'));
  const ports = readConfig(process.env);
  base = `http://127.0.0.1:${ports.backendPort}`;
  dashboard = `http://127.0.0.1:${ports.port}`;
  // Never attach to or terminate an unrelated process occupying the runtime ports,
  // including an already running `npm run dev` runtime.
  for (const url of [base, dashboard]) {
    try {
      await fetch(url, { signal: AbortSignal.timeout(500) });
      throw new Error(
        `PANINI needs ${url}, but another process is using it. Stop the other PANINI runtime (npm run dev) and reopen PANINI.`,
      );
    } catch (error: any) {
      if (error.message.startsWith('PANINI needs')) throw error;
    }
  }
  const credentialsFile = join(app.getPath('userData'), 'credentials.bin');
  if (existsSync(credentialsFile) && safeStorage.isEncryptionAvailable()) {
    const saved = JSON.parse(safeStorage.decryptString(readFileSync(credentialsFile)));
    for (const name of ['OPENAI_API_KEY', 'AI_GATEWAY_API_KEY'])
      if (typeof saved[name] === 'string') process.env[name] = saved[name];
  }
  const native = app.isPackaged
    ? join(root, 'desktop')
    : join(root, 'panini-core/capabilities/desktop');
  const exe = (name: string) =>
    app.isPackaged
      ? join(native, name === 'Worker' ? 'worker' : 'broker', `Panini.Desktop.${name}.exe`)
      : join(
          native,
          `Panini.Desktop.${name}/bin/Release/net10.0-windows`,
          `Panini.Desktop.${name}.exe`,
        );
  const node = app.isPackaged ? join(root, 'node/node.exe') : process.env.PANINI_NODE!;
  if (!node || !existsSync(node))
    throw new Error('Start PANINI with npm run panini so the Node runtime can be located.');
  const env = {
    ...process.env,
    PANINI_RUNTIME_ROOT: root,
    PANINI_INSTANCE_ID: instance,
    PANINI_BOOTSTRAP_TOKEN: bootstrap,
    PANINI_PROFILE_ID: 'panini',
    PANINI_RELEASE_ID: releaseId,
    JEV_REV_DESKTOP_ENABLED: 'true',
    PANINI_DESKTOP_PIPE: `panini-${instance}`,
    PANINI_DESKTOP_SECRET: desktopSecret,
    PANINI_DESKTOP_WORKER: exe('Worker'),
    PANINI_NODE: node,
    PANINI_CONSOLE_SECRET: consoleSecret,
    BACKEND_PORT: String(ports.backendPort),
    FRONTEND_PORT: String(ports.port),
    SEARCH_PORT: String(ports.searchPort),
    ANONYMIZED_TELEMETRY: 'false',
    PANINI_JEV_SIDECAR: app.isPackaged
      ? join(root, 'frontend/jev_sidecar.cjs')
      : join(root, 'frontend/jev_sidecar.mjs'),
    ...(app.isPackaged
      ? { PANINI_BACKEND_EXECUTABLE: join(root, 'backend/Panini.Backend.exe') }
      : {}),
  };
  launch(exe('Broker'), [], {
    ...withoutCredentials(env),
    PANINI_DESKTOP_SECRET: desktopSecret,
  });
  // The same runtime as `npm run dev`: backend, search and the gateway that serves
  // the web console and the extension. This shell is its controller and avatar.
  launch(
    node,
    app.isPackaged
      ? [join(root, 'scripts/panini-runtime.mjs'), '--managed', '--production']
      : [join(root, 'frontend/server/main.mjs'), '--managed'],
    env,
  );
  client = new PaniniClient(base, instance);
  for (let attempt = 0; attempt < 120; attempt++) {
    if (runtimeFailed) throw new Error('A companion stopped. Quit and reopen PANINI.');
    try {
      const health = await Promise.all(
        [base, dashboard].map(async (origin) => {
          const response = await fetch(`${origin}/api/panini/health`, {
            signal: AbortSignal.timeout(1000),
          });
          if (!response.ok) throw new Error('Runtime is not ready.');
          return response.json();
        }),
      );
      if (health.some((value) => value.instance_id !== instance))
        throw new Error('Runtime identity mismatch.');
      await client.connect(bootstrap);
      connected = true;
      await signalPresence();
      break;
    } catch {
      await new Promise((resolve) => setTimeout(resolve, 500));
    }
  }
  if (!connected)
    throw new Error('PANINI could not start its backend. Check the environment and restart.');
  emit({ type: 'snapshot', data: await client.scoped('') });
  void client.events(stream.signal, async (event) => {
    emit(event);
    // Never jump open over the person's work: the approval is its own small dialog, and a
    // question makes the bubble pulse with a notification. Only the person opens PANINI.
    if (event.type === 'task.awaiting_confirmation') {
      if (!win.isVisible()) show();
      void confirmAction(event.data.confirmation_id);
    }
    if (event.type === 'task.needs_focus') void focusDesktop();
    if (event.type === 'task.needs_input' && !win.isVisible()) show();
    if (event.type === 'session.closed') connected = false;
  });
  // The backend accepts a renewal only with a heartbeat count higher than the last one it saw, and only
  // while the interface is alive. Renew only after a fresh heartbeat, so one late heartbeat between two
  // ticks never turns into a rejected renewal; a transient failure is retried on the next tick. The lease
  // (15 s) is the real deadline: control is declared lost only when the backend says it has already
  // ended, or when renewals keep failing for most of the lease.
  let renewedLiveness = -1;
  let renewFailures = 0;
  const debugLease = process.env.PANINI_DEBUG_LEASE === '1';
  renew = setInterval(async () => {
    if (!connected || Date.now() - lastHeartbeat > 6000 || liveness === renewedLiveness) {
      if (debugLease)
        console.log(
          `[lease] skip connected=${connected} heartbeatAge=${Date.now() - lastHeartbeat} liveness=${liveness} renewed=${renewedLiveness}`,
        );
      return;
    }
    const revision = liveness;
    const started = Date.now();
    try {
      await client.scoped('/lease/renew', {
        command_id: randomUUID(),
        renderer_revision: revision,
      });
      renewedLiveness = revision;
      renewFailures = 0;
      if (debugLease) console.log(`[lease] renewed rev=${revision} in ${Date.now() - started}ms`);
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error);
      if (debugLease)
        console.log(
          `[lease] renew failed rev=${revision} after ${Date.now() - started}ms: ${detail}`,
        );
      // The backend reports the lease already ended: nothing left to retry.
      const ended = /disconnected|LEASE_EXPIRED|LEASE_REQUIRED|not found/i.test(detail);
      renewFailures += 1;
      if (!ended && renewFailures < 4) return;
      connected = false;
      emit({
        type: 'controller.lost',
        data: { message: 'Control expired. Reconnect to continue.', detail },
      });
    }
  }, 3000);
}
async function focusDesktop() {
  try {
    const target = await client.scoped('/desktop/focus-target');
    const host = app.isPackaged
      ? join(root, 'desktop/host/Panini.RuntimeHost.exe')
      : join(
          root,
          'panini-core/capabilities/desktop/Panini.RuntimeHost/bin/Release/net10.0-windows/Panini.RuntimeHost.exe',
        );
    // The direct child inherits Windows' foreground eligibility from the shell gesture.
    // It validates window identity and requests focus; it cannot inject input.
    const helper = spawn(host, ['--focus', JSON.stringify(target)], {
      windowsHide: true,
      stdio: 'ignore',
      env: withoutCredentials(process.env),
    });
    const timeout = setTimeout(() => helper.kill(), 2000);
    const unavailable = () =>
      emit({
        type: 'desktop.focus_required',
        data: {
          message: 'Bring the selected application forward to continue.',
        },
      });
    helper.once('exit', (code) => {
      clearTimeout(timeout);
      if (code !== 0) unavailable();
    });
    helper.once('error', () => {
      clearTimeout(timeout);
      unavailable();
    });
  } catch {
    emit({
      type: 'desktop.focus_required',
      data: { message: 'Bring the selected application forward to continue.' },
    });
  }
}
async function confirmAction(id: string) {
  if (confirmations.has(id) || !connected) return;
  confirmations.add(id);
  try {
    const pending = await client.scoped(`/confirmations/${encodeURIComponent(id)}`);
    const answer = await dialog.showMessageBox(win, {
      type: 'question',
      title: 'PANINI confirmation',
      message: pending.application,
      detail: `${pending.effect}\n\nData: ${pending.data || '(none)'}`,
      buttons: ['Cancel', 'Approve action'],
      defaultId: 0,
      cancelId: 0,
      noLink: true,
    });
    await client.scoped(`/confirmations/${encodeURIComponent(id)}`, {
      command_id: randomUUID(),
      nonce: pending.nonce,
      digest: pending.digest,
      decision: answer.response === 1 ? 'approve' : 'reject',
    });
  } catch {
    emit({
      type: 'confirmation.expired',
      data: { message: 'The confirmation is no longer active.' },
    });
  } finally {
    confirmations.delete(id);
  }
}
function validSender(event: Electron.IpcMainInvokeEvent | Electron.IpcMainEvent) {
  if (
    event.sender !== win.webContents ||
    event.senderFrame !== win.webContents.mainFrame ||
    !event.senderFrame.url.startsWith('panini://app/')
  )
    throw new Error('Untrusted sender');
}
function text(value: unknown, max = 5000) {
  if (typeof value !== 'string' || !value.trim() || value.length > max)
    throw new Error('Invalid text.');
  return value;
}
function ipc() {
  ipcMain.on('panini:heartbeat', (event) => {
    validSender(event);
    liveness++;
    lastHeartbeat = Date.now();
  });
  ipcMain.handle('panini:invoke', async (event, operation: string, input: any = {}) => {
    validSender(event);
    if (JSON.stringify(input).length > 120000) throw new Error('Request too large.');
    if (operation === 'credentials') {
      if (!safeStorage.isEncryptionAvailable())
        throw new Error('Windows credential encryption is unavailable.');
      const values = {
        OPENAI_API_KEY: text(input.openai, 500),
        AI_GATEWAY_API_KEY: text(input.gateway, 500),
      };
      writeFileSync(
        join(app.getPath('userData'), 'credentials.bin'),
        safeStorage.encryptString(JSON.stringify(values)),
      );
      app.relaunch();
      app.quit();
      return { saved: true };
    }
    if (operation === 'window') {
      if (input.action === 'move') {
        // Dragging the bubble: the renderer reports pointer movement in screen pixels.
        const dx = Math.round(Number(input.dx) || 0),
          dy = Math.round(Number(input.dy) || 0);
        const pos = win.getBounds();
        win.setPosition(pos.x + dx, pos.y + dy);
        return;
      }
      if (input.action === 'moved') {
        const pos = win.getBounds();
        preferences.bubble = { x: pos.x, y: pos.y };
        save();
        return;
      }
      if (input.action === 'attention') {
        // PANINI needs the person (an approval or a question): the bubble glows, and a
        // notification says why. Nothing steals focus.
        if (!win.isVisible()) win.showInactive();
        win.flashFrame(true);
        if (Notification.isSupported()) {
          const note = new Notification({
            title: 'PANINI needs you',
            body: String(input.message || 'A task is waiting for your answer.').slice(0, 200),
          });
          note.on('click', () => {
            show(true);
            resize(true);
          });
          note.show();
        }
        return;
      }
      if (input.action === 'hide') {
        win.hide();
        // Say where PANINI went and how to get it back, once per launch.
        if (!hideExplained && tray) {
          hideExplained = true;
          tray.displayBalloon({
            iconType: 'info',
            title: 'PANINI is still running',
            content:
              'Click the PANINI icon here in the tray, or press Ctrl+Shift+Space, to bring it back.',
          });
        }
      } else if (input.action === 'expand') resize(Boolean(input.value));
      else if (input.action === 'view') {
        if (!['stage', 'compact'].includes(input.value)) throw new Error('Invalid view');
        if (input.value === 'compact' && staged()) {
          // Leaving the stage: return the card to where it was.
          const area = screen.getPrimaryDisplay().workArea;
          win.setBounds({
            x: preferences.x ?? area.x + area.width - 450,
            y: preferences.y ?? area.y + 32,
            width: 430,
            height: 690,
          });
        }
        preferences.view = input.value;
        save();
        resize(true);
        emit({ type: 'shell.view', data: { view: input.value } });
      } else if (input.action === 'pin') {
        preferences.alwaysOnTop = Boolean(input.value);
        win.setAlwaysOnTop(preferences.alwaysOnTop);
        save();
      } else if (input.action === 'motion') {
        preferences.reducedMotion = Boolean(input.value);
        save();
      } else if (input.action === 'execution-mode') {
        if (!['compact', 'expanded', 'external'].includes(input.value))
          throw new Error('Invalid execution visibility');
        preferences.executionMode = input.value;
        resize(expanded);
        save();
      } else if (input.action === 'delegation-overlay') {
        preferences.delegationOverlay = Boolean(input.value);
        save();
      } else if (input.action === 'delegate') {
        showDelegation(String(input.value || ''));
      } else if (input.action === 'presence') {
        if (!['avatar', 'background'].includes(input.value)) throw new Error('Invalid presence');
        setPresence(input.value);
      } else if (input.action === 'microphone') {
        preferences.microphone = typeof input.value === 'string' ? input.value.slice(0, 300) : '';
        save();
      } else if (input.action === 'microphone-state') {
        tray?.setToolTip(
          input.value === 'on'
            ? 'PANINI — Microphone on'
            : input.value === 'muted'
              ? 'PANINI — Microphone muted'
              : 'PANINI — Microphone off',
        );
      }
      return preferences;
    }
    if (operation === 'admin') {
      if (!connected || runtimeFailed)
        throw new Error('PANINI is still connecting. Open Admin when the conversation is ready.');
      // PANINI Admin, already signed in: pairing, keys, models, usage and every setting.
      const page =
        typeof input?.page === 'string' && /^\/[a-z/-]*$/.test(input.page) ? input.page : '/';
      await shell.openExternal(consoleLink(page));
      return;
    }
    if (operation === 'continuity') {
      if (!connected || runtimeFailed)
        throw new Error('PANINI is still connecting. Try again when the conversation is ready.');
      if (
        input.action === 'seen' &&
        typeof input.item_id === 'string' &&
        /^(notice|result|handoff):[A-Za-z0-9_-]{1,200}$/.test(input.item_id)
      )
        return client.call(`/harness/presence/items/${encodeURIComponent(input.item_id)}/seen`, {});
      if (
        (input.action === 'send_to_phone' || input.action === 'call_me') &&
        typeof input.thread_id === 'string' &&
        /^[A-Za-z0-9_-]{1,200}$/.test(input.thread_id)
      )
        return client.call('/harness/presence/handoff', {
          thread_id: input.thread_id,
          destination: input.action === 'call_me' ? 'call' : 'phone',
        });
      throw new Error('Unsupported continuity action.');
    }
    if (operation === 'live') {
      // Live view (V2-12): the renderer has no token, so main relays only well-formed live paths.
      const path = String(input.path || '');
      if (
        !/^\/harness\/live(\/(chrome|managed|sandbox|desktop|android)\/[\w.-]{1,64}(\/(control(\/(take|release))?|input\/(click|type|key|scroll)|latest\.jpg))?)?$/.test(
          path,
        )
      )
        throw new Error('Unsupported live path.');
      if (input.action === 'frame') {
        const response = await fetch(`${client.base}/api/panini${path}/latest.jpg`, {
          headers: {
            Authorization: `Bearer ${client.token}`,
            'X-Panini-Instance': client.instance,
          },
          signal: AbortSignal.timeout(10000),
        });
        if (!response.ok) return null;
        const h = response.headers;
        return {
          seq: Number(h.get('x-live-seq') || 0),
          url: decodeURIComponent(h.get('x-live-url') || ''),
          title: decodeURIComponent(h.get('x-live-title') || ''),
          width: Number(h.get('x-live-width') || 0),
          height: Number(h.get('x-live-height') || 0),
          frame: Buffer.from(await response.arrayBuffer()).toString('base64'),
        };
      }
      return input.action === 'post' ? client.call(path, input.body || {}) : client.call(path);
    }
    if (operation === 'execution-viewer') {
      await executionViewer.open();
      return { opened: true };
    }
    if (operation === 'execution-copy') {
      clipboard.writeText(executionViewer.copyText());
      return { copied: true };
    }
    if (operation === 'status')
      return { connected, preferences, snapshot: connected ? await client.scoped('') : null };
    if (operation === 'reconnect') {
      if (runtimeFailed) throw new Error('Quit and reopen PANINI to restart its companions.');
      if (!client?.token) throw new Error('Restart PANINI to reconnect.');
      const snapshot = await client.scoped('');
      if (snapshot.state === 'closed') {
        const next = await client.call('/sessions', { command_id: randomUUID() });
        client.sessionId = next.session_id;
        client.cursor = 0;
      }
      const lease = await client.scoped('/lease/acquire', { command_id: randomUUID() });
      client.epoch = lease.lease_epoch;
      connected = true;
      await signalPresence();
      return client.scoped('');
    }
    if (!connected) throw new Error('PANINI is connecting.');
    switch (operation) {
      case 'capabilities':
        return client.call('/capabilities');
      case 'targets':
        if (!['browser', 'desktop', 'code'].includes(input.capability))
          throw new Error('Invalid capability');
        return client.call(`/targets?capability=${input.capability}`);
      case 'select':
        if (!['browser', 'desktop', 'code'].includes(input.capability))
          throw new Error('Invalid capability');
        return client.scoped('/targets', {
          command_id: randomUUID(),
          capability: input.capability,
          choice_id: text(input.choice_id, 200),
        });
      case 'turn':
        if (
          input.capability &&
          !['browser', 'desktop', 'search', 'code'].includes(input.capability)
        )
          throw new Error('Invalid capability');
        return client.scoped('/commands', {
          command_id: randomUUID(),
          text: text(input.text),
          capability_hint: input.capability || null,
          resume_choice_id: input.resume_choice_id ? text(input.resume_choice_id, 200) : null,
          expected_task_id: input.expected_task_id ? text(input.expected_task_id, 100) : null,
        });
      case 'speech-status':
        return client.scoped('/stt/status');
      case 'transcribe': {
        const audio = String(input?.audio_b64 || '');
        if (!audio.startsWith('data:audio/') || audio.length > 14_000_000)
          throw new Error('Record up to 30 seconds of audio for dictation.');
        return client.scoped('/stt/transcribe', { audio_b64: audio }, undefined, 95000);
      }
      case 'synthesize-local': {
        const spoken = text(input?.text, 500);
        return client.scoped('/tts/synthesize', { text: spoken }, undefined, 30000);
      }
      case 'synthesize-handoff':
        return client.scoped('/tts/handoff', {}, undefined, 30000);
      case 'feedback':
        return client.scoped('/feedback', {
          command_id: randomUUID(),
          task_id: String(input.task_id || ''),
          rating: input.rating === 'up' ? 'up' : 'down',
          note: String(input.note || '').slice(0, 1000),
        });
      case 'stop':
        return client.scoped('/stop', { command_id: randomUUID() });
      case 'teachStop':
        return client.call('/harness/teach/stop', {});
      case 'end':
        return client.scoped('/end', { command_id: randomUUID() });
      case 'media':
        return client.scoped('/media', {
          command_id: randomUUID(),
          media_generation: input.generation,
          sdp: text(input.sdp, 100000),
          persona_id: String(input.persona_id || 'panini'),
        });
      case 'voice-personas':
        return client.scoped('/voice-personas', undefined, 'GET');
      case 'media-close':
        return client.scoped(
          '/media',
          { command_id: randomUUID(), media_generation: input.generation },
          'DELETE',
        );
      case 'dashboard': {
        const { ticket } = await client.scoped('/observers', {
          command_id: randomUUID(),
          share_captions: Boolean(input.shareCaptions),
        });
        // Sign the console in, then land on this conversation's live activity.
        await shell.openExternal(consoleLink(`/activity#${encodeURIComponent(ticket)}`));
        return;
      }
      case 'pair': {
        const { code, expires_in } = await client.call('/extensions/pairing-code', {
          command_id: randomUUID(),
        });
        const answer = await dialog.showMessageBox(win, {
          title: 'Pair JevRev Claw',
          message: `Enter this code in the JevRev Claw extension: ${code}`,
          detail: `Pairing is needed once per Chrome profile. The code works once and expires in ${Math.round(expires_in / 60)} minutes.`,
          buttons: ['Copy code', 'Close'],
        });
        if (answer.response === 0) clipboard.writeText(code);
        return;
      }
      case 'open-result': {
        const snapshot = await client.scoped('');
        const result = snapshot.results.find((v: any) => v.result_id === input.result_id);
        if (!result) throw new Error('Result expired');
        const url = new URL(result.url);
        if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password)
          throw new Error('Unsupported address');
        await shell.openExternal(url.href);
        return;
      }
      default:
        throw new Error('Unsupported operation.');
    }
  });
}
if (locked)
  void app.whenReady().then(async () => {
    try {
      preferences = JSON.parse(readFileSync(prefFile(), 'utf8'));
    } catch {}
    const rendererRoot = resolve(__dirname, '../renderer');
    protocol.handle('panini', (request) => {
      const url = new URL(request.url);
      const file = resolve(
        rendererRoot,
        '.' + decodeURIComponent(url.pathname === '/' ? '/index.html' : url.pathname),
      );
      if (url.host !== 'app' || !file.startsWith(rendererRoot + sep))
        return new Response('Not found', { status: 404 });
      return net.fetch(pathToFileURL(file).href);
    });
    session.defaultSession.setPermissionRequestHandler((contents, permission, callback, details) =>
      callback(
        contents === win?.webContents &&
          permission === 'media' &&
          'mediaTypes' in details &&
          details.mediaTypes?.every((type: string) => type === 'audio') === true &&
          details.requestingUrl.startsWith('panini://app/'),
      ),
    );
    session.defaultSession.setPermissionCheckHandler(
      (contents, permission, origin) =>
        contents === win?.webContents && permission === 'media' && origin === 'panini://app',
    );
    const area = screen.getPrimaryDisplay().workArea;
    win = new BrowserWindow({
      width: 430,
      height: Math.min(690, area.height - 32),
      x: preferences.x ?? area.x + area.width - 450,
      y: preferences.y ?? area.y + 32,
      frame: false,
      transparent: true,
      show: false,
      resizable: false,
      alwaysOnTop: Boolean(preferences.alwaysOnTop),
      // The PANINI mark in the taskbar and Alt+Tab (not Electron's default).
      icon: join(__dirname, '../renderer/brand/panini-icon.ico'),
      skipTaskbar: false,
      webPreferences: {
        preload: join(__dirname, 'preload.cjs'),
        nodeIntegration: false,
        contextIsolation: true,
        sandbox: true,
        webSecurity: true,
        backgroundThrottling: false,
        devTools: !app.isPackaged,
      },
    });
    resize(true);
    win.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
    win.webContents.on('will-navigate', (event) => event.preventDefault());
    win.on('close', (event) => {
      if (!quitting) {
        event.preventDefault();
        save();
        win.hide();
      }
    });
    win.on('moved', save);
    win.webContents.on('render-process-gone', () => {
      connected = false;
      void client?.scoped('/lease/release', { command_id: randomUUID() }).catch(() => {});
    });
    ipc();
    await win.loadURL('panini://app/');
    if (preferences.presence !== 'background') show();
    const icon = nativeImage.createFromPath(
      join(__dirname, '../renderer/brand/panini-tray-32.png'),
    );
    tray = new Tray(icon);
    trayMenu();
    setInterval(() => void pollHarness(), 5000);
    const signalTrigger = (kind: string) =>
      void client.call('/harness/triggers/os', { kind }).catch(() => {});
    let wasOnline = net.isOnline();
    setInterval(() => {
      const online = net.isOnline();
      if (online !== wasOnline) {
        wasOnline = online;
        signalTrigger(online ? 'online' : 'offline');
      }
    }, 2000);
    pcLocked = powerMonitor.getSystemIdleState(120) === 'locked';
    powerMonitor.on('lock-screen', () => {
      pcLocked = true;
      void signalPresence();
      signalTrigger('locked');
    });
    powerMonitor.on('unlock-screen', () => {
      pcLocked = false;
      void signalPresence();
      signalTrigger('unlocked');
    });
    powerMonitor.on('suspend', () => {
      pcSuspended = true;
      void signalPresence();
      signalTrigger('suspended');
    });
    powerMonitor.on('resume', () => {
      pcSuspended = false;
      void signalPresence();
      signalTrigger('resumed');
    });
    powerMonitor.on('on-ac', () => signalTrigger('on_ac'));
    powerMonitor.on('on-battery', () => signalTrigger('on_battery'));
    win.on('show', () => void signalPresence());
    win.on('hide', () => void signalPresence());
    void signalPresence();
    setInterval(() => void signalPresence(), 15000);
    setInterval(() => void pollContinuity(), 1000);
    tray.on('click', () => show(true));
    screen.on('display-removed', () => resize(expanded));
    screen.on('display-metrics-changed', () => resize(expanded));
    globalShortcut.register('CommandOrControl+Shift+Space', () => {
      show(true);
      resize(true);
    });
    globalShortcut.register('CommandOrControl+Shift+Escape', () => {
      void client?.scoped('/stop', { command_id: randomUUID() }).catch(() => {});
    });
    app.on('second-instance', () => show(true));
    try {
      await startRuntime();
    } catch (error: any) {
      failRuntime(error.message);
    }
  });
app.on('before-quit', (event) => {
  if (quitting) return;
  event.preventDefault();
  quitting = true;
  executionViewer.close();
  save();
  clearInterval(renew);
  stream.abort();
  globalShortcut.unregisterAll();
  void (async () => {
    try {
      if (connected)
        await Promise.race([
          client.scoped('/end', { command_id: randomUUID() }),
          new Promise((resolve) => setTimeout(resolve, 2500)),
        ]);
    } finally {
      stopOwnedRuntime();
      app.quit();
    }
  })();
});
