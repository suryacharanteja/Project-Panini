import React, { useEffect, useState } from 'react';
import { LiveView, type LiveHand, type LiveTransport } from '../../../shared-ui';

/** Live view in the desktop shell (V2-12): the same LiveView card, bridged through the main process. */
const api = (window as any).panini;

const ipcTransport: LiveTransport = {
  subscribe(path, fps, onFrame, onState) {
    let alive = true;
    let last = 0;
    const tick = async () => {
      while (alive) {
        try {
          const frame = await api.invoke('live', { action: 'frame', path });
          onState(Boolean(frame));
          if (frame && frame.seq !== last) {
            last = frame.seq;
            onFrame(frame);
          }
        } catch {
          onState(false);
        }
        await new Promise((resolve) => setTimeout(resolve, 1000 / Math.min(fps, 2)));
      }
    };
    void tick();
    return () => {
      alive = false;
    };
  },
  get: (path) => api.invoke('live', { action: 'get', path }),
  post: (path, body) => api.invoke('live', { action: 'post', path, body }),
};

type Target = { hand: LiveHand; target: string; watchedRemotely?: boolean };

export function ShellLive() {
  const [targets, setTargets] = useState<Target[]>([]);
  useEffect(() => {
    let alive = true;
    const load = () =>
      ipcTransport
        .get('/harness/live')
        .then((view) => alive && setTargets(view?.targets ?? []))
        .catch(() => alive && setTargets([])); // Live view off or PANINI offline: show nothing.
    load();
    const timer = window.setInterval(load, 4000);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, []);
  if (!targets.length) return null;
  const watched = targets.filter(
    (t) => t.watchedRemotely && (t.hand === 'chrome' || t.hand === 'desktop'),
  );
  return (
    <section className="shell-live" aria-label="Live view">
      {watched.map((t) => (
        <p key={`w-${t.hand}`} className="pc-hint" role="status">
          Watching from your phone: {t.hand === 'chrome' ? 'your Chrome' : 'your desktop'}.
        </p>
      ))}
      {targets.map((t) => (
        <LiveView
          key={`${t.hand}/${t.target}`}
          base="/harness"
          hand={t.hand}
          target={t.target}
          fps={2}
          transport={ipcTransport}
        />
      ))}
    </section>
  );
}
