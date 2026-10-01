// Visual test harness: every avatar state side by side, with synthetic audio levels.
// Run: npx vite panini-core/apps/shell/test/gallery
import { createRoot } from 'react-dom/client';
import { Avatar, type AvatarState } from '../../src/renderer/avatar/Avatar';

const STATES: [AvatarState, string][] = [
  ['offline', ''],
  ['idle', ''],
  ['listening', ''],
  ['thinking', ''],
  ['working', 'search'],
  ['working', 'code'],
  ['working', 'agent'],
  ['approval', 'browser'],
  ['success', ''],
  ['blocked', 'desktop'],
  ['muted', ''],
];
const wave = (speed: number) => () => 0.5 + 0.5 * Math.sin((performance.now() / 1000) * speed) ** 2;
const params = new URLSearchParams(location.search);
const speaking = params.has('speaking');

function Tile({
  state,
  capability,
  size,
}: {
  state: AvatarState;
  capability: string;
  size: number;
}) {
  return (
    <figure style={{ margin: 0, textAlign: 'center' }} data-state={state}>
      <div
        style={{
          position: 'relative',
          width: size,
          height: size,
          margin: '0 auto',
          borderRadius: '50%',
          background: 'radial-gradient(circle, #1c1612, #0a0a0c)',
        }}
      >
        <div style={{ position: 'absolute', inset: `-${size * 0.18}px` }}>
          <Avatar
            state={state}
            capability={capability}
            micLevel={wave(3)}
            voiceLevel={speaking ? wave(5) : () => 0}
          />
        </div>
      </div>
      <figcaption
        style={{
          fontFamily: 'Consolas, monospace',
          fontSize: 11,
          color: '#d4905a',
          marginTop: size * 0.22,
        }}
      >
        {state}
        {capability ? ` · ${capability}` : ''}
      </figcaption>
    </figure>
  );
}

createRoot(document.getElementById('root')!).render(
  <div style={{ padding: 24 }}>
    <h1 style={{ fontWeight: 300, letterSpacing: 4, color: '#e8b89a' }}>
      PANINI · avatar states{speaking ? ' · speaking' : ''}
    </h1>
    <div
      style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fill, minmax(170px, 1fr))',
        gap: 36,
      }}
    >
      {STATES.map(([s, c], i) => (
        <Tile key={i} state={s} capability={c} size={120} />
      ))}
    </div>
    <h2 style={{ fontWeight: 300, color: '#9a9aa3', marginTop: 40 }}>
      Capsule size (node cluster)
    </h2>
    <div style={{ display: 'flex', gap: 28 }}>
      {STATES.map(([s, c], i) => (
        <Tile key={i} state={s} capability={c} size={40} />
      ))}
    </div>
  </div>,
);

