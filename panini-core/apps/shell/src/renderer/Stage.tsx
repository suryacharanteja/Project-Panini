import { useCallback, useEffect, useRef, useState } from 'react';
import { Avatar, type AvatarState, type StageGeometry } from './avatar/Avatar';

/* The full-screen Algorithmic Avatar ("Web Console" layout from the PANINI design system):
   Lexicon (telemetry) on the left, the Engine (avatar) in the centre, Capability quadrants
   on the right, with the conversation docked beneath. */

export const STATE_CAPTIONS: Record<AvatarState, [string, string]> = {
  offline: ['Connecting', 'Connecting to your workspace'],
  idle: ['Ready', 'Tell PANINI what you want to accomplish'],
  listening: ['Listening', 'Your microphone is on'],
  thinking: ['Planning', 'Preparing the next step'],
  working: ['Working', 'Follow progress in the conversation'],
  approval: ['Needs your input', 'Review the request in the conversation'],
  success: ['Verified complete', 'Review the result and its evidence'],
  blocked: ['Needs attention', 'Review what happened and the next step below'],
  muted: ['Microphone muted', 'Unmute to continue talking'],
};

type Capability = { id: string; label: string; deva: string; sub: string };
export const STAGE_CAPABILITIES: Capability[] = [
  { id: 'search', label: 'Search', deva: 'अन्वेषण', sub: 'Find information with sources' },
  { id: 'browser', label: 'Browser', deva: 'जाल', sub: 'Use your connected browser' },
  { id: 'desktop', label: 'Desktop', deva: 'पटल', sub: 'Work with desktop apps' },
  { id: 'code', label: 'Code', deva: 'सूत्र', sub: 'Work with code and files' },
];

/** Line-art icons per the design system, rendered with a 1px copper glow. */
function CapabilityIcon({ id }: { id: string }) {
  const common = {
    fill: 'none',
    stroke: 'currentColor',
    strokeWidth: 1.4,
    strokeLinecap: 'round' as const,
  };
  return (
    <svg viewBox="0 0 40 40" width="30" height="30" aria-hidden="true">
      {id === 'search' && (
        <g {...common}>
          <circle cx="20" cy="20" r="4" />
          {[0, 60, 120, 180, 240, 300].map((a) => {
            const r = (a * Math.PI) / 180;
            return (
              <g key={a}>
                <line
                  x1={20 + Math.cos(r) * 6}
                  y1={20 + Math.sin(r) * 6}
                  x2={20 + Math.cos(r) * 13}
                  y2={20 + Math.sin(r) * 13}
                />
                <circle cx={20 + Math.cos(r) * 15} cy={20 + Math.sin(r) * 15} r="2" />
              </g>
            );
          })}
        </g>
      )}
      {id === 'browser' && (
        <g {...common}>
          <circle cx="20" cy="20" r="13" />
          <ellipse cx="20" cy="20" rx="6" ry="13" />
          <line x1="7" y1="20" x2="33" y2="20" />
          <line x1="9" y1="13" x2="31" y2="13" />
          <line x1="9" y1="27" x2="31" y2="27" />
        </g>
      )}
      {id === 'desktop' && (
        <g {...common}>
          <rect x="6" y="8" width="18" height="13" rx="1.5" />
          <rect x="11" y="13" width="18" height="13" rx="1.5" />
          <rect x="16" y="18" width="18" height="13" rx="1.5" />
        </g>
      )}
      {id === 'code' && (
        <g {...common}>
          <circle cx="20" cy="8" r="2.5" />
          <circle cx="11" cy="20" r="2.5" />
          <circle cx="29" cy="20" r="2.5" />
          <circle cx="6" cy="32" r="2.5" />
          <circle cx="16" cy="32" r="2.5" />
          <circle cx="29" cy="32" r="2.5" />
          <path d="M18.5 10 12.5 18M21.5 10 27.5 18M10 22.5 7 29.5M12 22.5 15 29.5M29 22.5V29.5" />
        </g>
      )}
    </svg>
  );
}

export function useClock() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const timer = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(timer);
  }, []);
  return now;
}

export function StageTitle({ state }: { state: AvatarState }) {
  const now = useClock();
  return (
    <div className="stage-title" aria-hidden="true">
      <span>Your workspace</span>
      <small>
        {STATE_CAPTIONS[state][0]} ·{' '}
        {now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
      </small>
    </div>
  );
}

export function Backdrop() {
  const columns = [
    'अइउण् ऋऌक् एओङ् ऐऔच्',
    'हयवरट् लण् ञमङणनम्',
    'झभञ् घढधष् जबगडदश्',
    'खफछठथचटतव् कपय्',
    'शषसर् हल् ॥ इति माहेश्वराणि सूत्राणि ॥',
    'वृद्धिरादैच् ॥ अदेङ्गुणः ॥',
    'इको यणचि ॥ तस्मिन्निति निर्दिष्टे पूर्वस्य ॥',
  ];
  return (
    <div className="stage-backdrop" aria-hidden="true">
      <svg className="ghost-network" viewBox="0 0 400 400">
        {[...Array(14)].map((_, i) => {
          const a = (i / 14) * Math.PI * 2;
          const x = 200 + Math.cos(a) * 150;
          const y = 200 + Math.sin(a) * 150;
          const b = ((i + 3) / 14) * Math.PI * 2;
          return (
            <g key={i}>
              <line x1={x} y1={y} x2={200 + Math.cos(b) * 150} y2={200 + Math.sin(b) * 150} />
              <line x1={x} y1={y} x2="200" y2="200" />
              <circle cx={x} cy={y} r="6" />
            </g>
          );
        })}
        <circle cx="200" cy="200" r="150" />
      </svg>
      <div className="sutra-columns">
        {columns.map((text, i) => (
          <span key={i} style={{ animationDelay: `${-i * 7}s` }}>
            {text}
          </span>
        ))}
      </div>
    </div>
  );
}

export function Lexicon({ log, caps }: { log: string[]; caps: any[] }) {
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => {
    end.current?.scrollIntoView({ block: 'end' });
  }, [log.length]);
  return (
    <aside className="lexicon" aria-label="Runtime telemetry">
      <div className="panel-title">Runtime diagnostics</div>
      <div className="lexicon-stream" role="log" aria-live="off">
        {log.map((line, i) => (
          <div
            key={i}
            className={
              line.includes('ERROR') || line.includes('BLOCKED')
                ? 'bad'
                : line.includes('VERIFIED') || line.includes('COMPLETED')
                  ? 'good'
                  : ''
            }
          >
            {line}
          </div>
        ))}
        <div ref={end} className="cursor">
          ▍
        </div>
      </div>
      <div className="panel-title">Tool connections</div>
      <ul className="lexicon-caps">
        {caps.map((c) => (
          <li key={c.capability}>
            <span className={c.state === 'ready' ? 'dot on' : 'dot'} />
            {c.capability.toUpperCase()}
            <em>{c.state === 'ready' ? 'OK' : 'SETUP'}</em>
          </li>
        ))}
      </ul>
    </aside>
  );
}

export function Capabilities({
  caps,
  active,
  state,
  selected,
  onSelect,
  registerAnchor,
}: {
  caps: any[];
  active: string;
  state: AvatarState;
  selected: string;
  onSelect: (id: string) => void;
  registerAnchor: (id: string, element: HTMLElement | null) => void;
}) {
  const hot = state === 'working' || state === 'approval';
  return (
    <nav className="quadrants" aria-label="Capabilities">
      <div className="panel-title">Tools</div>
      {STAGE_CAPABILITIES.map((cap) => {
        const info = caps.find((c) => c.capability === cap.id);
        const isActive = hot && (active === cap.id || active === 'agent');
        return (
          <button
            key={cap.id}
            type="button"
            className={`quadrant ${isActive ? 'active' : ''} ${selected === cap.id ? 'selected' : ''} ${info?.state === 'ready' ? 'ready' : 'setup'}`}
            aria-pressed={selected === cap.id}
            title={info?.reason || `Work with ${cap.label}`}
            onClick={() => onSelect(selected === cap.id ? '' : cap.id)}
          >
            <span
              className="quadrant-icon"
              ref={(element) => {
                registerAnchor(cap.id, element);
              }}
            >
              <CapabilityIcon id={cap.id} />
            </span>
            <span className="quadrant-text">
              <strong>{cap.label}</strong>
              <small>{cap.sub}</small>
              <em>{isActive ? 'ACTIVE' : info?.state === 'ready' ? 'READY' : 'NEEDS SETUP'}</em>
            </span>
          </button>
        );
      })}
    </nav>
  );
}

/** The large avatar, drawn across the whole stage so threads can reach the capability cards. */
export function Engine({
  state,
  capability,
  micLevel,
  voiceLevel,
  reducedMotion,
  anchors,
  onActivate,
  listening,
}: {
  state: AvatarState;
  capability: string;
  micLevel: () => number;
  voiceLevel: () => number;
  reducedMotion: boolean;
  anchors: React.MutableRefObject<Record<string, HTMLElement | null>>;
  /** Clicking the core starts or ends the voice conversation. */
  onActivate?: () => void;
  listening?: boolean;
}) {
  const layer = useRef<HTMLDivElement>(null);
  const core = useRef<HTMLElement>(null);
  const geometry = useCallback((): StageGeometry | null => {
    const base = layer.current?.getBoundingClientRect();
    const centre = core.current?.getBoundingClientRect();
    if (!base || !centre) return null;
    const points: StageGeometry['anchors'] = {};
    for (const [id, element] of Object.entries(anchors.current)) {
      const r = element?.getBoundingClientRect();
      if (r)
        points[id] = { x: r.left - base.left + r.width / 2, y: r.top - base.top + r.height / 2 };
    }
    return {
      cx: centre.left - base.left + centre.width / 2,
      cy: centre.top - base.top + centre.height / 2,
      size: Math.min(centre.width, centre.height),
      anchors: points,
    };
  }, [anchors]);
  return (
    <>
      <div className="engine-layer" ref={layer} aria-hidden="true">
        <Avatar
          state={state}
          capability={capability}
          micLevel={micLevel}
          voiceLevel={voiceLevel}
          reducedMotion={reducedMotion}
          geometry={geometry}
        />
      </div>
      <section
        className="engine"
        aria-label={`PANINI is ${STATE_CAPTIONS[state][1].toLowerCase()}`}
      >
        <div className="panel-title">PANINI</div>
        <button
          type="button"
          className="engine-core"
          ref={core as unknown as React.RefObject<HTMLButtonElement>}
          onClick={onActivate}
          aria-label={listening ? 'Stop voice conversation' : 'Talk to PANINI'}
          title={listening ? 'Stop voice conversation' : 'Talk to PANINI'}
        />
        <div className="engine-caption">
          <strong>{STATE_CAPTIONS[state][0]}</strong>
          <span>{STATE_CAPTIONS[state][1]}</span>
          <small>
            {listening ? 'Select the avatar to end the call' : 'Select the avatar to talk'}
          </small>
        </div>
      </section>
    </>
  );
}
