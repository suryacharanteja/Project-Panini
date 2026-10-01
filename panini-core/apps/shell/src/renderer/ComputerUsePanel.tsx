import React, { useEffect, useState } from 'react';

const labels: Record<string, string> = {
  observed: 'Observed', proposed: 'Proposed', dispatched: 'Dispatched', complete: 'Verified',
  incomplete: 'Incomplete', error: 'Failed', timeout: 'Timed out', cancelled: 'Stopped', step: 'Step',
};

/**
 * One line of text for an activity event. `action` is an object (an empty one for "observed"), a string,
 * or missing, and only text may reach the DOM: rendering the object itself throws in React and would
 * take the whole interface (and its liveness heartbeat) down with it.
 */
export function describeActivity(event: any): string {
  const action = event?.action;
  if (action && typeof action === 'object') {
    const kind = typeof action.kind === 'string' ? action.kind.replace(/_/g, ' ') : '';
    return kind || (typeof event.message === 'string' && event.message) || 'Activity';
  }
  if (typeof action === 'string' && action) return action;
  return (typeof event?.message === 'string' && event.message) || 'Activity';
}

export function ComputerUsePanel({ events, phase }: { events: any[]; phase: string }) {
  const [selected, setSelected] = useState(0);
  useEffect(() => setSelected(Math.max(0, events.length - 1)), [events.length]);
  if (!events.length) return null;
  const current = events[selected] || events[events.length - 1];
  const frame = current.frame_b64 ? `data:image/jpeg;base64,${current.frame_b64}` : '';
  return <section className="computer-use" aria-label="Live computer use">
    <div className="computer-use-heading"><span className="eyebrow">LIVE COMPUTER USE</span><strong>{labels[current.phase] || String(current.phase || '')}</strong><small>{phase === 'completed' ? 'Trace retained' : 'Live trace'}</small></div>
    <div className="computer-use-frame">
      {frame ? <img src={frame} alt={`Observed ${current.surface || 'computer'} state`} /> : <p>Frame unavailable; the action and evidence record remain available.</p>}
      {typeof current.action?.x === 'number' && <span className="computer-use-cursor" style={{ left: `${current.action.x}px`, top: `${current.action.y}px` }}>+</span>}
    </div>
    <ol className="computer-use-timeline">
      {events.map((event, index) => <li key={event.sequence || index} className={index === selected ? 'selected' : ''}><button onClick={() => setSelected(index)}><b>{labels[event.phase] || String(event.phase || '')}</b><span>{describeActivity(event)}</span></button></li>)}
    </ol>
    <p className="computer-use-note">{String(current.message || 'No explanatory detail was supplied.')}{current.frame_omitted ? ' Full frame retained only in local evidence.' : ''}</p>
  </section>;
}

/**
 * A panel that cannot render is dropped, never the interface around it. Without this, one malformed
 * event unmounts the avatar, its heartbeat stops and PANINI loses control of a running task.
 */
export class PanelBoundary extends React.Component<{ children: React.ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  componentDidCatch(error: unknown) {
    console.error('A live panel failed to render and was hidden.', error);
  }
  render() {
    return this.state.failed ? null : this.props.children;
  }
}
