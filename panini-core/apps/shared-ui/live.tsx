import { useEffect, useRef, useState } from 'react';
import { CardBoundary } from './boundary';

/** Live view and takeover for any hand (V2-12): frames over SSE, Take / Give back, owner input on the frame. */

export type LiveHand = 'chrome' | 'managed' | 'sandbox' | 'desktop' | 'android';
export type Frame = {
  seq: number;
  url?: string;
  title?: string;
  width?: number;
  height?: number;
  frame: string;
};
type ControlView = {
  holder: 'agent' | 'human';
  resumeSnapshotRequired?: boolean;
  requested?: boolean;
};

export const HAND_NAMES: Record<LiveHand, string> = {
  chrome: 'Your Chrome',
  managed: "PANINI's browser",
  sandbox: 'Sandbox computer',
  desktop: 'Desktop',
  android: 'Phone',
};

// Hands whose input happens on the PC itself (your own Chrome tab and desktop): Take pauses PANINI; you use them directly.
const LOCAL_INPUT: LiveHand[] = ['chrome', 'desktop'];

/** How a surface reaches the live routes. Browsers use SSE and fetch; the desktop shell bridges through its main process. */
export type LiveTransport = {
  subscribe: (
    path: string,
    fps: number,
    onFrame: (frame: Frame) => void,
    onState: (up: boolean) => void,
  ) => () => void;
  get: (path: string) => Promise<any>;
  post: (path: string, body?: unknown) => Promise<any>;
};

export const webTransport: LiveTransport = {
  subscribe(path, fps, onFrame, onState) {
    const source = new EventSource(`${path}/stream?fps=${fps}`, { withCredentials: true });
    source.onopen = () => onState(true);
    source.onerror = () => onState(false);
    source.addEventListener('frame', (event) => onFrame(JSON.parse((event as MessageEvent).data)));
    return () => source.close();
  },
  get: (path) => fetch(path, { credentials: 'same-origin' }).then((r) => (r.ok ? r.json() : null)),
  post: (path, body) => post(path, body),
};

async function post(url: string, body: unknown = {}) {
  const response = await fetch(url, {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!response.ok) throw new Error((await response.text()).slice(0, 200));
  return response.json();
}

function LiveViewBody({ base, hand, target, fps = 4, transport = webTransport }: LiveViewProps) {
  const path = `${base}/live/${hand}/${encodeURIComponent(target)}`;
  const [frame, setFrame] = useState<Frame | null>(null);
  const [control, setControl] = useState<ControlView>({ holder: 'agent' });
  const [connected, setConnected] = useState(false);
  const [text, setText] = useState('');
  const [error, setError] = useState('');
  const image = useRef<HTMLImageElement>(null);
  const holding = control.holder === 'human';
  const remoteInput = holding && !LOCAL_INPUT.includes(hand);

  useEffect(() => transport.subscribe(path, fps, setFrame, setConnected), [path, fps, transport]);

  useEffect(() => {
    let alive = true;
    const load = () =>
      transport
        .get(`${path}/control`)
        .then((view) => alive && view && setControl(view))
        .catch(() => {});
    load();
    const timer = window.setInterval(load, 2000);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, [path, transport]);

  const act = (run: () => Promise<unknown>) =>
    run().then(
      () => setError(''),
      (e: Error) => setError(e.message),
    );
  const send = (action: string, body: unknown) =>
    act(() => transport.post(`${path}/input/${action}`, body));
  const change = (verb: 'take' | 'release') =>
    act(() => transport.post(`${path}/control/${verb}`).then(setControl));

  return (
    <section className="pc-card pc-live" aria-label={`Live view: ${HAND_NAMES[hand]}`}>
      <header>
        <strong>{HAND_NAMES[hand]}</strong>
        <span className={`pc-chip ${connected && frame ? 'pc-chip-working' : ''}`}>
          {connected && frame ? '● live' : 'waiting for frames'}
        </span>
        {holding && <span className="pc-chip pc-chip-attention">you have control</span>}
      </header>
      {frame?.url && (
        <p className="pc-detail pc-live-url" title={frame.title || frame.url}>
          {frame.title ? `${frame.title} · ` : ''}
          {frame.url}
        </p>
      )}
      {frame ? (
        <img
          ref={image}
          className={`pc-live-frame ${remoteInput ? 'pc-live-input' : ''}`}
          src={`data:image/${frame.frame.startsWith('iVBOR') ? 'png' : 'jpeg'};base64,${frame.frame}`}
          alt={`Live view of ${HAND_NAMES[hand]}`}
          onClick={(event) => {
            if (!remoteInput || !image.current) return;
            const box = image.current.getBoundingClientRect();
            const width = frame.width || image.current.naturalWidth;
            const height = frame.height || image.current.naturalHeight;
            void send('click', {
              x: Math.round(((event.clientX - box.left) / box.width) * width),
              y: Math.round(((event.clientY - box.top) / box.height) * height),
            });
          }}
          onWheel={(event) =>
            remoteInput && void send('scroll', { deltaY: Math.round(event.deltaY) })
          }
        />
      ) : (
        <p className="pc-hint">
          Frames appear here while PANINI uses {HAND_NAMES[hand].toLowerCase()}.
        </p>
      )}
      <div className="pc-actions">
        {holding ? (
          <button type="button" onClick={() => change('release')}>
            Give back
          </button>
        ) : (
          <button type="button" onClick={() => change('take')}>
            Take control
          </button>
        )}
        {holding && LOCAL_INPUT.includes(hand) && (
          <span className="pc-hint">PANINI is paused. Use it on this PC, then give it back.</span>
        )}
      </div>
      {remoteInput && (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            void send('type', { text }).then(() => setText(''));
          }}
        >
          <input
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="Type into the page"
            aria-label="Type into the page"
          />
          <button type="submit" disabled={!text}>
            Type
          </button>
          {['Enter', 'Tab', 'Backspace', 'Escape'].map((key) => (
            <button key={key} type="button" onClick={() => void send('key', { key })}>
              {key}
            </button>
          ))}
        </form>
      )}
      {control.resumeSnapshotRequired && !holding && (
        <p className="pc-hint">Handed back: PANINI looks again before its next action.</p>
      )}
      {error && <p className="pc-hint pc-live-error">{error}</p>}
    </section>
  );
}

export type LiveViewProps = {
  base: string;
  hand: LiveHand;
  target: string;
  fps?: number;
  transport?: LiveTransport;
};

export function LiveView(props: LiveViewProps) {
  return (
    <CardBoundary label="live view">
      <LiveViewBody {...props} />
    </CardBoundary>
  );
}
