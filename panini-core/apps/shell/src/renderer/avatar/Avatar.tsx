import { useEffect, useRef } from 'react';
import { PALETTE } from '../../../../phone/lib/persona.mjs';
import {
  buildP,
  CAPABILITIES,
  project,
  SHIVA_SUTRAS,
  type Vec3,
} from '../../../../admin/src/genesis/model';

/** Every moment the avatar can express. Driven by real runtime events and audio. */
export type AvatarState =
  | 'offline' // Connecting: the P keeps deriving itself.
  | 'idle' // Dhatu: copper core breathing at 12 BPM.
  | 'listening' // Cymatic mandala reacting to the live microphone (amber).
  | 'thinking' // Parsing: concentric rings counter-rotating.
  | 'working' // A sutra thread from the root to the active capability.
  | 'approval' // Waiting for the person: still core, steady amber halo, thread paused.
  | 'success' // Gold flash, the network reassembles.
  | 'blocked' // Thread snaps, copper dims. Calm, not alarming.
  | 'muted'; // Mic muted: hollow root, slow breath.

type Props = {
  state: AvatarState;
  personaColor?: string;
  /** search | browser | desktop | code | agent (agent fires to all four). */
  capability?: string;
  /** 0..1 live levels; read every frame. */
  micLevel?: () => number;
  voiceLevel?: () => number;
  reducedMotion?: boolean;
  /** Full-screen stage: where the core sits and where each capability card is, in canvas
   * pixels (measured from the layout every frame). Without it, the avatar fills its box. */
  geometry?: () => StageGeometry | null;
};

export type StageGeometry = {
  cx: number;
  cy: number;
  size: number;
  anchors: Record<string, { x: number; y: number }>;
};

const AMBER = [255, 191, 0];
const GOLD = [212, 175, 55];
const rgba = (c: number[], a: number) => `rgba(${c[0]},${c[1]},${c[2]},${a})`;
const mix = (a: number[], b: number[], t: number) =>
  a.map((v, i) => Math.round(v + (b[i]! - v) * t));
const ease = (t: number) => (t >= 1 ? 1 : 1 - 2 ** (-10 * t));

/** Capability anchors on the avatar's rim (in emblem units), matching the Genesis layout. */
function anchor(index: number): Vec3 {
  const angle = CAPABILITIES[index]!.angle;
  return { x: Math.cos(angle) * 1.28, y: Math.sin(angle) * 0.98 - 0.05, z: 0.1 };
}

export function Avatar({
  state,
  personaColor = 'copper',
  capability = '',
  micLevel,
  voiceLevel,
  reducedMotion,
  geometry,
}: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const live = useRef({
    state,
    personaColor,
    capability,
    micLevel,
    voiceLevel,
    reducedMotion,
    geometry,
  });
  live.current = { state, personaColor, capability, micLevel, voiceLevel, reducedMotion, geometry };

  useEffect(() => {
    const canvas = canvasRef.current!;
    const ctx = canvas.getContext('2d')!;
    const graph = buildP();
    let COPPER = [184, 115, 51],
      GLOW = [232, 184, 154],
      DIM = [120, 90, 70];
    // Centre the emblem on its own bounding box (the P's geometry is not centred on 0,0).
    {
      const xs = graph.nodes.map((n) => n.x);
      const ys = graph.nodes.map((n) => n.y);
      const midX = (Math.min(...xs) + Math.max(...xs)) / 2;
      const midY = (Math.min(...ys) + Math.max(...ys)) / 2;
      const span = Math.max(Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys));
      for (const n of graph.nodes) {
        n.x = ((n.x - midX) / span) * 2;
        n.y = ((n.y - midY) / span) * 2;
      }
    }
    const root = graph.order[0]!;
    const born = new Map<number, number>();
    graph.order.forEach((node, i) => born.set(node, 0.2 + i * 0.035));
    const scatter = graph.nodes.map((_, i) => {
      const a = i * 2.399963;
      return { x: Math.cos(a) * 2, y: Math.sin(a) * 1.7, z: ((i % 5) - 2) * 0.4 };
    });
    let width = 0;
    let height = 0;
    let frame = 0;
    let last = 0;
    let smoothMic = 0;
    let smoothVoice = 0;
    let previousState: AvatarState = live.current.state;
    let previousCap = '';
    let changedAt = 0; // When the state last changed (seconds).
    let capChangedAt = -10;
    let deriveFrom = 0; // Start of the current assembly.
    const t0 = performance.now();
    // Interaction: the network leans toward the pointer and nearby neurons reach for it.
    const pointer = { x: -1e4, y: -1e4, seen: 0 };
    const onPointer = (event: PointerEvent) => {
      const rect = canvas.getBoundingClientRect();
      pointer.x = event.clientX - rect.left;
      pointer.y = event.clientY - rect.top;
      pointer.seen = performance.now();
    };
    window.addEventListener('pointermove', onPointer, { passive: true });
    // Synaptic sparks travelling along the connections.
    type Spark = { edge: number; t: number; speed: number; forward: boolean };
    const sparks: Spark[] = [];
    let sparkBudget = 0;
    const osReduced = window.matchMedia('(prefers-reduced-motion: reduce)');

    const resize = () => {
      const rect = canvas.getBoundingClientRect();
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      width = Math.min(rect.width, 4096);
      height = Math.min(rect.height, 4096);
      canvas.width = Math.max(1, Math.round(width * dpr));
      canvas.height = Math.max(1, Math.round(height * dpr));
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };

    /** At tray/capsule sizes the P is illegible: the Visual Lexicon's 4-point node cluster. */
    function drawCluster(time: number, s: AvatarState, color: number[], breath: number) {
      const cx = width / 2;
      const cy = height / 2;
      const r = Math.min(width, height) * 0.3;
      const points = [0, 1, 2].map((i) => {
        const a = -Math.PI / 2 + (i * Math.PI * 2) / 3 + (s === 'thinking' ? time * 1.5 : 0);
        return { x: cx + Math.cos(a) * r, y: cy + Math.sin(a) * r };
      });
      const level = s === 'listening' ? smoothMic : smoothVoice;
      ctx.lineWidth = 1.5;
      for (const p of points) {
        ctx.strokeStyle = rgba(color, 0.55 + 0.3 * breath);
        ctx.beginPath();
        ctx.moveTo(cx, cy);
        ctx.lineTo(p.x, p.y);
        ctx.stroke();
        ctx.fillStyle = rgba(color, 0.85);
        ctx.beginPath();
        ctx.arc(p.x, p.y, 2.4, 0, Math.PI * 2);
        ctx.fill();
      }
      ctx.shadowColor = rgba(color, 1);
      ctx.shadowBlur = 8 + 10 * breath + level * 20;
      ctx.fillStyle = rgba(s === 'muted' ? DIM : GLOW, 1);
      ctx.beginPath();
      ctx.arc(cx, cy, 3.6 + level * 3, 0, Math.PI * 2);
      if (s === 'muted') {
        ctx.strokeStyle = rgba(DIM, 1);
        ctx.stroke();
      } else ctx.fill();
      ctx.shadowBlur = 0;
    }

    function draw(now: number) {
      const { state: s, capability: cap } = live.current;
      const token = live.current.personaColor as keyof typeof PALETTE;
      const custom = Object.hasOwn(PALETTE, token) && token !== 'copper';
      COPPER = custom ? PALETTE[token] : PALETTE.copper;
      GLOW = custom ? mix(COPPER, [255, 255, 255], 0.45) : [232, 184, 154];
      DIM = custom ? COPPER.map((v) => Math.round(v * 0.55)) : [120, 90, 70];
      const still = !!live.current.reducedMotion || osReduced.matches;
      const time = still ? 30 : (now - t0) / 1000;
      if (s !== previousState) {
        if (s === 'offline' || previousState === 'offline') deriveFrom = time;
        previousState = s;
        changedAt = time;
      }
      if (cap !== previousCap) {
        previousCap = cap;
        capChangedAt = time;
      }
      const since = time - changedAt;
      if (s === 'offline' && time - deriveFrom > 6) deriveFrom = time;
      const target = Math.min(1, Math.max(0, live.current.micLevel?.() ?? 0));
      const voiceTarget = Math.min(1, Math.max(0, live.current.voiceLevel?.() ?? 0));
      smoothMic += (target - smoothMic) * 0.25;
      smoothVoice += (voiceTarget - smoothVoice) * 0.25;
      const speaking = smoothVoice > 0.03 && s !== 'offline';

      ctx.clearRect(0, 0, width, height);
      const breath =
        0.6 + 0.4 * (0.5 + 0.5 * Math.sin((time * Math.PI * 2) / (s === 'muted' ? 8 : 5)));
      const tone =
        s === 'listening' || s === 'approval'
          ? AMBER
          : s === 'success' && since < 1.4
            ? mix(GOLD, COPPER, Math.min(1, since / 1.4))
            : s === 'blocked' || s === 'muted'
              ? DIM
              : COPPER;

      if (Math.min(width, height) < 72) {
        drawCluster(time, s, tone, breath);
        return;
      }

      const stage = live.current.geometry?.() ?? null;
      const cx = stage ? stage.cx : width / 2;
      const cy = stage ? stage.cy : height / 2 + height * 0.03;
      const scale = stage ? stage.size * 0.34 : Math.min(width, height) * 0.27;

      // Stage: a slow field of copper dust orbiting the core (anti-gravity particles).
      if (stage && !still) {
        ctx.globalCompositeOperation = 'lighter';
        for (let k = 0; k < 220; k++) {
          const a = k * 2.399963 + time * (0.03 + (k % 7) * 0.006);
          const r = scale * (1.3 + ((k * 7919) % 100) / 45);
          const x = cx + Math.cos(a) * r;
          const y = cy + Math.sin(a) * r * 0.62;
          const alpha = 0.05 + ((k * 31) % 10) / 70;
          ctx.fillStyle = rgba(GLOW, alpha);
          ctx.fillRect(x, y, 1.3, 1.3);
        }
        ctx.globalCompositeOperation = 'source-over';
      }
      const near = !still && now - pointer.seen < 4000;
      const leanX = near ? Math.max(-1, Math.min(1, (pointer.x - cx) / (scale * 4))) : 0;
      const leanY = near ? Math.max(-1, Math.min(1, (pointer.y - cy) / (scale * 4))) : 0;
      const yaw = still ? 0.25 : Math.sin(time * 0.35) * 0.3 + leanX * 0.45;
      const pitch = still ? 0 : Math.sin(time * 0.23) * 0.06 + leanY * 0.25;

      // Spatial shell: a transparent latitude field behind the living emblem.
      if (stage) {
        ctx.save();
        ctx.translate(cx, cy);
        const radius = scale * 1.38;
        const rotation = still ? 0.18 : time * 0.025 + leanX * 0.08;
        ctx.rotate(rotation);
        ctx.strokeStyle = rgba(GLOW, 0.075);
        ctx.lineWidth = 0.7;
        for (let latitude = -3; latitude <= 3; latitude++) {
          const offset = latitude / 4;
          const reach = Math.sqrt(1 - offset * offset) * radius;
          ctx.beginPath();
          ctx.ellipse(0, offset * radius * 0.88, reach, reach * 0.17, 0, 0, Math.PI * 2);
          ctx.stroke();
        }
        for (let meridian = 0; meridian < 5; meridian++) {
          ctx.beginPath();
          ctx.ellipse(0, 0, radius * (0.18 + meridian * 0.18), radius, 0.28, 0, Math.PI * 2);
          ctx.stroke();
        }
        const aura = ctx.createRadialGradient(0, 0, radius * 0.12, 0, 0, radius * 1.2);
        aura.addColorStop(0, rgba(tone, s === 'listening' || s === 'working' ? 0.12 : 0.055));
        aura.addColorStop(1, rgba(tone, 0));
        ctx.fillStyle = aura;
        ctx.fillRect(-radius * 1.2, -radius * 1.2, radius * 2.4, radius * 2.4);
        ctx.restore();
      }

      // Tilted orbital layers give the emblem depth without obscuring its silhouette.
      if (stage) {
        ctx.save();
        ctx.translate(cx, cy);
        for (let orbit = 0; orbit < 3; orbit++) {
          ctx.save();
          ctx.rotate((orbit - 1) * 0.48 + (still ? 0 : Math.sin(time * 0.12) * 0.08));
          const r = scale * (1.28 + orbit * 0.15);
          ctx.strokeStyle = rgba(orbit === 1 ? GLOW : COPPER, 0.18);
          ctx.lineWidth = orbit === 1 ? 1.2 : 0.7;
          ctx.beginPath();
          ctx.ellipse(0, 0, r, r * (0.48 + orbit * 0.12), 0, 0, Math.PI * 2);
          ctx.stroke();
          const angle = time * (0.1 + orbit * 0.04) + orbit * 2;
          ctx.fillStyle = rgba(GLOW, 0.7);
          ctx.shadowColor = rgba(COPPER, 0.7);
          ctx.shadowBlur = 10;
          ctx.beginPath();
          ctx.arc(
            Math.cos(angle) * r,
            Math.sin(angle) * r * (0.48 + orbit * 0.12),
            2,
            0,
            Math.PI * 2,
          );
          ctx.fill();
          ctx.restore();
        }
        ctx.restore();
      }

      // HUD scanner (stage): thin ticked rings rotating in opposite directions and a slow
      // radar sweep. Calm instrumentation around the core, never over the emblem.
      if (stage && !still) {
        ctx.globalCompositeOperation = 'lighter';
        for (const [radius, ticks, dir, alpha] of [
          [1.32, 72, 1, 0.22],
          [1.52, 36, -1, 0.16],
        ] as const) {
          const r = scale * radius;
          const spin = time * 0.08 * dir;
          ctx.strokeStyle = rgba(COPPER, alpha * 0.6);
          ctx.lineWidth = 1;
          ctx.beginPath();
          ctx.ellipse(cx, cy, r, r * 0.96, 0, spin, spin + Math.PI * 1.55);
          ctx.stroke();
          for (let k = 0; k < ticks; k++) {
            const a = (k / ticks) * Math.PI * 2 + spin;
            const long = k % 6 === 0;
            const inner = r - (long ? 7 : 3);
            ctx.strokeStyle = rgba(long ? GLOW : COPPER, alpha * (long ? 1 : 0.6));
            ctx.beginPath();
            ctx.moveTo(cx + Math.cos(a) * inner, cy + Math.sin(a) * inner * 0.96);
            ctx.lineTo(cx + Math.cos(a) * r, cy + Math.sin(a) * r * 0.96);
            ctx.stroke();
          }
        }
        const gradient = ctx.createConicGradient(time * 0.6, cx, cy);
        gradient.addColorStop(0, rgba(tone, 0.12));
        gradient.addColorStop(0.08, rgba(tone, 0));
        gradient.addColorStop(1, rgba(tone, 0));
        ctx.fillStyle = gradient;
        ctx.beginPath();
        ctx.ellipse(cx, cy, scale * 1.3, scale * 1.25, 0, 0, Math.PI * 2);
        ctx.fill();
        ctx.globalCompositeOperation = 'source-over';
      }
      ctx.globalCompositeOperation = 'lighter';

      // Listening (Concept 2): a cymatic mandala of particles shaped by the live voice.
      if (s === 'listening') {
        const level = 0.15 + smoothMic * 0.85;
        const petals = 6 + Math.round(smoothMic * 6) * 2;
        for (let ring = 0; ring < 5; ring++) {
          const base = scale * (0.75 + ring * 0.19);
          const count = 60 + ring * 18;
          for (let k = 0; k < count; k++) {
            const a = (k / count) * Math.PI * 2 + (ring % 2 ? -1 : 1) * time * (0.2 + ring * 0.05);
            const wave = Math.sin(a * petals + time * 3 + ring) * level * scale * 0.12;
            const r = base + wave;
            const alpha = (0.2 + 0.6 * level) * (1 - ring * 0.14);
            ctx.fillStyle = rgba(ring % 2 ? AMBER : GLOW, alpha);
            ctx.fillRect(cx + Math.cos(a) * r, cy + Math.sin(a) * r * 0.92, 1.6, 1.6);
          }
        }
      }

      // Thinking (parsing): concentric rings snapping and counter-rotating.
      if (s === 'thinking' || (s === 'offline' && !still)) {
        for (let ring = 0; ring < 3; ring++) {
          const r = scale * (1.05 + ring * 0.17);
          const dir = ring % 2 ? -1 : 1;
          ctx.strokeStyle = rgba(ring === 1 ? GLOW : COPPER, 0.4 - ring * 0.08);
          ctx.lineWidth = 1.2;
          ctx.setLineDash([r * 0.35, r * 0.18]);
          ctx.lineDashOffset = dir * time * 40 * (ring + 1);
          ctx.beginPath();
          ctx.ellipse(cx, cy, r, r * 0.94, 0, 0, Math.PI * 2);
          ctx.stroke();
        }
        ctx.setLineDash([]);
      }

      // The emblem. It derives itself while connecting, fragments briefly on delegation,
      // and reassembles on success.
      const fragment =
        s === 'working' && time - capChangedAt < 0.9
          ? Math.sin(((time - capChangedAt) / 0.9) * Math.PI)
          : 0;
      const positions = graph.nodes.map((node, i) => {
        // While connecting, the P derives itself from the root again and again.
        const local = time - deriveFrom - born.get(i)!;
        const assembled = s === 'offline' && !still ? ease(Math.max(0, local / 1.1)) : 1;
        const from = scatter[i]!;
        const radial = 1 + fragment * 0.28;
        const lift = still ? 0 : Math.sin(time * 1.1 + i) * 0.01;
        return project(
          {
            x: from.x + (node.x * radial - from.x) * assembled,
            y: from.y + (node.y * radial - from.y) * assembled + lift,
            z: from.z + (node.z - from.z) * assembled,
          },
          yaw,
          pitch,
          scale,
          cx,
          cy,
        );
      });
      const dim = s === 'listening' ? 0.5 : s === 'blocked' || s === 'muted' ? 0.55 : 1;
      const unit = Math.max(0.75, Math.min(1.7, scale / 70)); // Neuron size follows the emblem.
      const activity =
        s === 'working'
          ? 1
          : s === 'thinking' || s === 'offline'
            ? 0.7
            : s === 'approval'
              ? 0.15
              : 0.25;
      const energy = activity + smoothMic * 0.8 + smoothVoice * 0.8;

      // Hairline connections, brighter near the pointer.
      ctx.globalCompositeOperation = 'lighter';
      for (const [a, b] of graph.edges) {
        const pa = positions[a]!;
        const pb = positions[b]!;
        const mx = (pa.x + pb.x) / 2;
        const my = (pa.y + pb.y) / 2;
        const reach = near
          ? Math.max(0, 1 - Math.hypot(pointer.x - mx, pointer.y - my) / (scale * 0.9))
          : 0;
        ctx.strokeStyle = rgba(
          mix(COPPER, GLOW, 0.35 + reach * 0.5),
          (0.3 + 0.12 * breath + reach * 0.4) * dim,
        );
        ctx.lineWidth = 0.8 * unit * ((pa.depth + pb.depth) / 2);
        ctx.beginPath();
        ctx.moveTo(pa.x, pa.y);
        ctx.lineTo(pb.x, pb.y);
        ctx.stroke();
      }

      // Synaptic sparks: the network is always thinking a little; more when PANINI works,
      // listens or speaks.
      if (!still && s !== 'offline') {
        sparkBudget += (1.5 + energy * 14) / 60;
        while (sparkBudget >= 1 && sparks.length < 90) {
          sparkBudget -= 1;
          sparks.push({
            edge: Math.floor(Math.random() * graph.edges.length),
            t: 0,
            speed: 0.9 + Math.random() * 1.4 + energy,
            forward: Math.random() < 0.5,
          });
        }
        for (let k = sparks.length - 1; k >= 0; k--) {
          const spark = sparks[k]!;
          spark.t += spark.speed / 60;
          if (spark.t >= 1) {
            sparks.splice(k, 1);
            continue;
          }
          const [a, b] = graph.edges[spark.edge]!;
          const from = positions[spark.forward ? a : b]!;
          const to = positions[spark.forward ? b : a]!;
          const x = from.x + (to.x - from.x) * spark.t;
          const y = from.y + (to.y - from.y) * spark.t;
          const tail = Math.max(0, spark.t - 0.25);
          ctx.strokeStyle = rgba(AMBER, 0.55);
          ctx.lineWidth = 1.2 * unit;
          ctx.beginPath();
          ctx.moveTo(from.x + (to.x - from.x) * tail, from.y + (to.y - from.y) * tail);
          ctx.lineTo(x, y);
          ctx.stroke();
          ctx.fillStyle = rgba([255, 236, 190], 0.95);
          ctx.beginPath();
          ctx.arc(x, y, 1.3 * unit, 0, Math.PI * 2);
          ctx.fill();
        }
      } else sparks.length = 0;

      // Neurons: small white-hot points with a copper halo.
      positions.forEach((p, i) => {
        const isRoot = i === root;
        const reach = near
          ? Math.max(0, 1 - Math.hypot(pointer.x - p.x, pointer.y - p.y) / (scale * 0.7))
          : 0;
        const pulse = 0.75 + 0.25 * Math.sin(time * 2.2 + i * 1.7);
        const core = (isRoot ? 4.2 : 1.6 + (i % 3) * 0.45) * unit * p.depth;
        const halo = core * (isRoot ? 5.5 + smoothMic * 4 + smoothVoice * 4 : 3.6 + reach * 3);
        const haloColor = isRoot && s !== 'muted' ? AMBER : tone;
        const glow = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, halo);
        glow.addColorStop(
          0,
          rgba(haloColor, (isRoot ? 0.55 : 0.32) * breath * pulse * dim + reach * 0.3),
        );
        glow.addColorStop(1, rgba(haloColor, 0));
        ctx.fillStyle = glow;
        ctx.beginPath();
        ctx.arc(p.x, p.y, halo, 0, Math.PI * 2);
        ctx.fill();
        if (isRoot && s === 'muted') {
          ctx.strokeStyle = rgba(DIM, 1);
          ctx.lineWidth = 1.4;
          ctx.beginPath();
          ctx.arc(p.x, p.y, core, 0, Math.PI * 2);
          ctx.stroke();
          return;
        }
        const body = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, core);
        body.addColorStop(0, rgba([255, 244, 226], 1));
        body.addColorStop(0.55, rgba(isRoot ? mix(AMBER, GLOW, 0.4) : GLOW, 0.95));
        body.addColorStop(1, rgba(COPPER, 0.85));
        ctx.fillStyle = body;
        ctx.beginPath();
        ctx.arc(p.x, p.y, core, 0, Math.PI * 2);
        ctx.fill();
      });

      // Reaching for the pointer: the nearest neurons send faint threads to it.
      if (near) {
        const nearest = positions
          .map((p, i) => ({ p, i, d: Math.hypot(pointer.x - p.x, pointer.y - p.y) }))
          .filter((n) => n.d < scale * 0.8)
          .sort((a, b) => a.d - b.d)
          .slice(0, 3);
        for (const n of nearest) {
          ctx.strokeStyle = rgba(GLOW, 0.35 * (1 - n.d / (scale * 0.8)));
          ctx.lineWidth = 0.7;
          ctx.beginPath();
          ctx.moveTo(n.p.x, n.p.y);
          ctx.lineTo(pointer.x, pointer.y);
          ctx.stroke();
        }
      }
      ctx.globalCompositeOperation = 'source-over';
      const rootPos = positions[root]!;
      // The Dhatu's ring, as in the logo: an open arc around the root node.
      ctx.strokeStyle = rgba(GLOW, (0.45 + 0.35 * breath) * (s === 'muted' ? 0.5 : 1));
      ctx.lineWidth = 1.2 * Math.max(0.75, Math.min(1.7, scale / 70));
      ctx.beginPath();
      ctx.arc(rootPos.x, rootPos.y, scale * 0.16 * rootPos.depth, -0.35, Math.PI * 1.6);
      ctx.stroke();
      ctx.globalCompositeOperation = 'lighter';

      // Capability anchors (visible only while PANINI is delegating or waiting on one).
      const capIndex = CAPABILITIES.findIndex((c) => c.id === cap);
      const fanOut = cap === 'agent';
      const showAnchors = s === 'working' || s === 'approval' || s === 'blocked';
      const anchors = CAPABILITIES.map((c, i) =>
        stage?.anchors[c.id]
          ? { ...stage.anchors[c.id]!, depth: 1, z: 0 }
          : project(anchor(i), yaw, pitch, scale, cx, cy),
      );
      // On the stage the capability cards are real UI; the canvas draws only the threads.
      if (showAnchors && !stage) {
        CAPABILITIES.forEach((c, i) => {
          const active = fanOut || i === capIndex;
          const p = anchors[i]!;
          ctx.strokeStyle = rgba(active ? tone : DIM, active ? 0.85 : 0.25);
          ctx.lineWidth = 1.2;
          ctx.beginPath();
          const size = 7;
          for (let k = 0; k <= 6; k++) {
            const a = (k / 6) * Math.PI * 2 + Math.PI / 6;
            const x = p.x + Math.cos(a) * size;
            const y = p.y + Math.sin(a) * size;
            if (k === 0) ctx.moveTo(x, y);
            else ctx.lineTo(x, y);
          }
          ctx.stroke();
          if (active && width > 110) {
            ctx.globalCompositeOperation = 'source-over';
            ctx.font = `600 8px 'JetBrains Mono', Consolas, monospace`;
            ctx.textAlign = 'center';
            ctx.fillStyle = rgba(GLOW, 0.8);
            ctx.fillText(c.label.toUpperCase(), p.x, p.y + 17);
            ctx.globalCompositeOperation = 'lighter';
          }
        });
      }

      // The sutra thread: root → capability. Pulses travel while working; it holds still
      // while waiting for approval; it snaps when blocked.
      const threadTargets = fanOut ? [0, 1, 2, 3] : capIndex >= 0 ? [capIndex] : [];
      for (const index of showAnchors ? threadTargets : []) {
        const end = anchors[index]!;
        const mid = {
          x: (rootPos.x + end.x) / 2 + (end.y - rootPos.y) * 0.25,
          y: (rootPos.y + end.y) / 2,
        };
        const point = (t: number) => ({
          x: (1 - t) * (1 - t) * rootPos.x + 2 * (1 - t) * t * mid.x + t * t * end.x,
          y: (1 - t) * (1 - t) * rootPos.y + 2 * (1 - t) * t * mid.y + t * t * end.y,
        });
        const drawn = s === 'working' ? ease(Math.min(1, (time - capChangedAt) / 0.7)) : 1;
        const snapped = s === 'blocked';
        ctx.lineWidth = 1.8;
        ctx.strokeStyle = rgba(tone, snapped ? 0.35 : 0.75);
        ctx.beginPath();
        for (let k = 0; k <= 30; k++) {
          const t = (k / 30) * drawn;
          if (snapped && t > 0.45 && t < 0.62) {
            ctx.stroke();
            ctx.beginPath();
            continue;
          }
          const q = point(t);
          const sag = snapped && t >= 0.62 ? (t - 0.62) * 18 : 0;
          if (k === 0) ctx.moveTo(q.x, q.y);
          else ctx.lineTo(q.x, q.y + sag);
        }
        ctx.stroke();
        if (s === 'working' && !still) {
          for (let pulse = 0; pulse < 3; pulse++) {
            const t = (time * 0.7 + pulse / 3 + index * 0.13) % 1;
            if (t > drawn) continue;
            const q = point(t);
            ctx.shadowColor = rgba(AMBER, 1);
            ctx.shadowBlur = 14;
            ctx.fillStyle = rgba(AMBER, 0.95);
            ctx.beginPath();
            ctx.arc(q.x, q.y, 2.2, 0, Math.PI * 2);
            ctx.fill();
            ctx.shadowBlur = 0;
          }
          if (CAPABILITIES[index]!.id === 'code' && width > 110) {
            ctx.globalCompositeOperation = 'source-over';
            ctx.font = `600 12px 'JetBrains Mono', Consolas, monospace`;
            ctx.fillStyle = rgba(AMBER, 0.6 + 0.4 * breath);
            ctx.fillText('{ }', end.x, end.y - 12);
            ctx.globalCompositeOperation = 'lighter';
          }
        }
      }

      // Approval: a steady amber halo holds around the core.
      if (s === 'approval') {
        ctx.strokeStyle = rgba(AMBER, 0.55 + 0.2 * Math.sin(time * Math.PI));
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.ellipse(cx, cy, scale * 1.12, scale * 1.06, 0, 0, Math.PI * 2);
        ctx.stroke();
      }

      // Success: a gold ring expands once as the network reassembles.
      if (s === 'success' && since < 1.2 && !still) {
        const t = since / 1.2;
        ctx.strokeStyle = rgba(GOLD, 0.8 * (1 - t));
        ctx.lineWidth = 3 * (1 - t) + 0.5;
        ctx.beginPath();
        ctx.ellipse(cx, cy, scale * (0.6 + t * 0.9), scale * (0.6 + t * 0.85), 0, 0, Math.PI * 2);
        ctx.stroke();
      }

      // Speaking (Concept 4): a ring of sutra characters orbits the core with PANINI's voice.
      if (speaking) {
        ctx.globalCompositeOperation = 'source-over';
        const chars = [...SHIVA_SUTRAS.replace(/\s/g, '')];
        const count = 28;
        const r = scale * (1.08 + smoothVoice * 0.18);
        ctx.font = `${Math.max(9, Math.round(scale * 0.13))}px 'Nirmala UI', 'Noto Sans Devanagari', sans-serif`;
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        for (let k = 0; k < count; k++) {
          const a = (k / count) * Math.PI * 2 + time * 0.35;
          const glow = 0.3 + 0.7 * Math.max(0, Math.sin(a * 3 - time * 6)) * smoothVoice;
          ctx.save();
          ctx.translate(cx + Math.cos(a) * r, cy + Math.sin(a) * r * 0.95);
          ctx.rotate(a + Math.PI / 2);
          ctx.fillStyle = rgba(glow > 0.6 ? GLOW : COPPER, 0.35 + glow * 0.6);
          ctx.fillText(chars[k % chars.length]!, 0, 0);
          ctx.restore();
        }
      }
      ctx.globalCompositeOperation = 'source-over';
    }

    const loop = (now: number) => {
      const s = live.current.state;
      // Idle and muted render at ~30 fps; active states at full rate. Hidden: nothing.
      const interval = s === 'idle' || s === 'muted' ? 33 : 0;
      if (!document.hidden && now - last >= interval) {
        last = now;
        draw(now);
      }
      frame = requestAnimationFrame(loop);
    };
    const observer = new ResizeObserver(() => {
      resize();
      draw(performance.now());
    });
    observer.observe(canvas);
    resize();
    frame = requestAnimationFrame(loop);
    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
      window.removeEventListener('pointermove', onPointer);
    };
  }, []);

  // Sized by layout, never by its own pixel buffer: otherwise each resize would feed back
  // into the next (device-pixel-ratio growth).
  return (
    <canvas
      ref={canvasRef}
      className="avatar-canvas"
      style={{ display: 'block', width: '100%', height: '100%' }}
      aria-hidden="true"
      data-avatar-state={state}
      data-persona-color={personaColor}
    />
  );
}

/** Live level (0..1) of an audio stream, for the listening and speaking visuals. */
export function levelMeter(stream: MediaStream | null) {
  if (!stream || !stream.getAudioTracks().length) return { read: () => 0, close: () => {} };
  const context = new AudioContext();
  const source = context.createMediaStreamSource(stream);
  const analyser = context.createAnalyser();
  analyser.fftSize = 256;
  source.connect(analyser);
  const data = new Uint8Array(analyser.fftSize);
  return {
    read() {
      analyser.getByteTimeDomainData(data);
      let sum = 0;
      for (const v of data) sum += ((v - 128) / 128) ** 2;
      return Math.min(1, Math.sqrt(sum / data.length) * 4);
    },
    close() {
      source.disconnect();
      void context.close();
    },
  };
}
