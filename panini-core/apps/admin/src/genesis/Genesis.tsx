import { useEffect, useRef } from 'react';
import { buildP, CAPABILITIES, project, SHIVA_SUTRAS, type Vec3 } from './model';

type Props = {
  /** 'working' fires sutra threads faster and warms the palette toward amber. */
  activity?: 'idle' | 'working';
  /** Capability ids that are ready; others are drawn dim. */
  ready?: string[];
  className?: string;
};

const COPPER = [184, 115, 51];
const GLOW = [232, 184, 154];
const AMBER = [255, 191, 0];
const rgba = (c: number[], a: number) => `rgba(${c[0]},${c[1]},${c[2]},${a})`;
const easeOutExpo = (t: number) => (t >= 1 ? 1 : 1 - 2 ** (-10 * t));

type Thread = { target: number; start: number; path: Vec3[]; flashed?: boolean };

/**
 * PANINI Genesis: the Algorithmic 'P' derives itself from the root node, breathes at
 * 12 BPM and fires glowing Sutra threads to the capability nodes it delegates to.
 * Canvas 2D with a small perspective projection: no WebGL dependency, strict-CSP safe.
 */
export function Genesis({ activity = 'idle', ready = [], className }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const state = useRef({ activity, ready });
  state.current = { activity, ready };

  useEffect(() => {
    const canvas = canvasRef.current!;
    const ctx = canvas.getContext('2d')!;
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const graph = buildP();
    const born = new Map<number, number>();
    graph.order.forEach((node, i) => born.set(node, 0.25 + i * 0.045));
    const starts = graph.nodes.map((_, i) => {
      const a = i * 2.399963; // Golden-angle scatter.
      return {
        x: Math.cos(a) * (2.2 + (i % 5) * 0.3),
        y: Math.sin(a) * 1.8,
        z: ((i % 7) - 3) * 0.4,
      };
    });
    const dust = Array.from({ length: 900 }, (_, i) => {
      const u = (i * 0.618033) % 1;
      const v = (i * 0.414213) % 1;
      const theta = u * Math.PI * 2;
      const phi = Math.acos(2 * v - 1);
      const r = 0.9 + ((i * 7919) % 100) / 60;
      return {
        x: Math.sin(phi) * Math.cos(theta) * r,
        y: Math.sin(phi) * Math.sin(theta) * r * 0.8,
        z: Math.cos(phi) * r * 0.6,
        speed: 0.05 + ((i * 31) % 17) / 200,
        size: 0.5 + ((i * 13) % 10) / 12,
      };
    });
    const columns: { x: number; y: number; speed: number; offset: number; alpha: number }[] = [];
    const threads: Thread[] = [];
    let width = 0;
    let height = 0;
    let dpr = 1;
    let frame = 0;
    let visible = true;
    let pointerX = 0;
    let pointerY = 0;
    let lastThread = 0;
    const flashes = new Map<number, number>();
    const t0 = performance.now();

    function resize() {
      const rect = canvas.getBoundingClientRect();
      dpr = Math.min(window.devicePixelRatio || 1, 2);
      width = rect.width;
      height = rect.height;
      canvas.width = Math.round(width * dpr);
      canvas.height = Math.round(height * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      columns.length = 0;
      const count = Math.max(6, Math.floor(width / 46));
      for (let i = 0; i < count; i++) {
        columns.push({
          x: (i + 0.5) * (width / count),
          y: -((i * 97) % 400),
          speed: 10 + ((i * 37) % 14),
          offset: (i * 11) % SHIVA_SUTRAS.length,
          alpha: 0.035 + ((i * 17) % 10) / 180,
        });
      }
    }

    function capabilityPoint(index: number, time: number): Vec3 {
      const cap = CAPABILITIES[index]!;
      const a = cap.angle + Math.sin(time * 0.07 + index) * 0.08;
      return {
        x: Math.cos(a) * 1.55,
        y: Math.sin(a) * 0.95 - 0.05,
        z: Math.sin(a * 2 + time * 0.2) * 0.25,
      };
    }

    function spawnThread(time: number) {
      const readyIds = state.current.ready;
      const choices = CAPABILITIES.map((c, i) => ({ c, i })).filter(
        ({ c }) => readyIds.length === 0 || readyIds.includes(c.id),
      );
      const pick = (choices.length ? choices : CAPABILITIES.map((c, i) => ({ c, i })))[
        Math.floor(((time * 7.31) % 1) * (choices.length || CAPABILITIES.length))
      ]!;
      const root = graph.nodes[graph.order[0]!]!;
      const hub = graph.nodes[graph.hub]!;
      const end = capabilityPoint(pick.i, time);
      const control = {
        x: (hub.x + end.x) / 2 + (end.y - hub.y) * 0.35,
        y: (hub.y + end.y) / 2,
        z: 0.4,
      };
      const path: Vec3[] = [root, hub];
      for (let s = 1; s <= 24; s++) {
        const t = s / 24;
        const a = (1 - t) * (1 - t);
        const b = 2 * (1 - t) * t;
        const c = t * t;
        path.push({
          x: a * hub.x + b * control.x + c * end.x,
          y: a * hub.y + b * control.y + c * end.y,
          z: a * hub.z + b * control.z + c * end.z,
        });
      }
      threads.push({ target: pick.i, start: time, path });
    }

    function draw(now: number) {
      const time = reduced ? 12 : (now - t0) / 1000;
      const working = state.current.activity === 'working';
      ctx.clearRect(0, 0, width, height);
      // Wide screens: the emblem sits right of centre, clear of the title at bottom left.
      const cx = width * (width > 900 ? 0.62 : 0.5);
      const cy = height * 0.4;
      const scale = Math.min(width * 0.55, height) * 0.27;
      const breath = 0.6 + 0.4 * (0.5 + 0.5 * Math.sin((time * Math.PI * 2) / 5)); // 12 BPM
      const yaw = Math.sin(time * 0.16) * 0.42 + pointerX * 0.35;
      const pitch = Math.sin(time * 0.11) * 0.08 + pointerY * 0.2;

      // Background: slow columns of the Śiva-sūtras.
      ctx.font = `15px 'Nirmala UI', 'Noto Sans Devanagari', sans-serif`;
      ctx.textAlign = 'center';
      for (const col of columns) {
        const y = reduced ? col.y + 200 : (col.y + time * col.speed) % (height + 400);
        for (let k = 0; k < 14; k++) {
          const ch = SHIVA_SUTRAS[(col.offset + k) % SHIVA_SUTRAS.length]!;
          if (ch === ' ') continue;
          const yy = y - k * 22;
          if (yy < -20 || yy > height + 20) continue;
          ctx.fillStyle = rgba(COPPER, col.alpha * (1 - k / 16));
          ctx.fillText(ch, col.x, yy);
        }
      }

      // Mandala rings rotating in opposite directions behind the emblem.
      ctx.save();
      ctx.translate(cx, cy);
      for (const [radius, sides, dir, alpha] of [
        [1.25, 12, 1, 0.08],
        [1.05, 8, -1, 0.1],
        [0.86, 16, 1, 0.05],
      ] as const) {
        ctx.beginPath();
        for (let s = 0; s <= sides; s++) {
          const a = (s / sides) * Math.PI * 2 + time * 0.04 * dir;
          const r = radius * scale;
          const px = Math.cos(a) * r;
          const py = Math.sin(a) * r * 0.62;
          if (s === 0) ctx.moveTo(px, py);
          else ctx.lineTo(px, py);
        }
        ctx.strokeStyle = rgba(COPPER, alpha);
        ctx.lineWidth = 1;
        ctx.setLineDash([2, 6]);
        ctx.stroke();
      }
      ctx.setLineDash([]);
      ctx.restore();

      // Copper dust: anti-gravity particles orbiting the core.
      ctx.globalCompositeOperation = 'lighter';
      for (const d of dust) {
        const a = time * d.speed;
        const p = project(
          {
            x: d.x * Math.cos(a) - d.z * Math.sin(a),
            y: d.y + Math.sin(time * 0.3 + d.x) * 0.03,
            z: d.x * Math.sin(a) + d.z * Math.cos(a),
          },
          yaw,
          pitch,
          scale,
          cx,
          cy,
        );
        const alpha = 0.12 + 0.28 * (p.depth - 0.7);
        ctx.fillStyle = rgba(GLOW, Math.max(0.03, Math.min(0.45, alpha)));
        ctx.fillRect(p.x, p.y, d.size * p.depth, d.size * p.depth);
      }

      // The emblem: nodes fly in along the derivation order, then edges appear.
      const positions = graph.nodes.map((node, i) => {
        const t = reduced ? 1 : easeOutExpo(Math.max(0, (time - born.get(i)!) / 1.4));
        const s = starts[i]!;
        const lift = Math.sin(time * 0.8 + i) * 0.008;
        return project(
          {
            x: s.x + (node.x - s.x) * t,
            y: s.y + (node.y - s.y) * t + lift,
            z: s.z + (node.z - s.z) * t,
          },
          yaw,
          pitch,
          scale,
          cx,
          cy,
        );
      });
      const arrived = (i: number) => reduced || time > born.get(i)! + 0.9;
      for (const [a, b] of graph.edges) {
        if (!arrived(a) || !arrived(b)) continue;
        const pa = positions[a]!;
        const pb = positions[b]!;
        const grow = reduced
          ? 1
          : Math.min(1, (time - Math.max(born.get(a)!, born.get(b)!) - 0.9) / 0.5);
        const gradient = ctx.createLinearGradient(pa.x, pa.y, pb.x, pb.y);
        gradient.addColorStop(0, rgba(GLOW, 0.55 * breath));
        gradient.addColorStop(1, rgba(COPPER, 0.5 * breath));
        ctx.strokeStyle = gradient;
        ctx.lineWidth = 1.4 * ((pa.depth + pb.depth) / 2);
        ctx.beginPath();
        ctx.moveTo(pa.x, pa.y);
        ctx.lineTo(pa.x + (pb.x - pa.x) * grow, pa.y + (pb.y - pa.y) * grow);
        ctx.stroke();
      }
      const rootIndex = graph.order[0]!;
      positions.forEach((p, i) => {
        const isRoot = i === rootIndex;
        const r = (isRoot ? 7 : 3.4 + (i % 3) * 0.6) * p.depth;
        ctx.shadowColor = rgba(isRoot ? AMBER : COPPER, 0.9);
        ctx.shadowBlur = (isRoot ? 28 : 12) * breath;
        const gradient = ctx.createRadialGradient(p.x - r / 3, p.y - r / 3, 0, p.x, p.y, r);
        gradient.addColorStop(0, rgba([255, 236, 214], 0.95));
        gradient.addColorStop(0.45, rgba(GLOW, 0.9));
        gradient.addColorStop(1, rgba(COPPER, 0.85));
        ctx.fillStyle = gradient;
        ctx.beginPath();
        ctx.arc(p.x, p.y, r, 0, Math.PI * 2);
        ctx.fill();
      });
      ctx.shadowBlur = 0;
      // The root's halo ring (the Dhatu).
      const root = positions[rootIndex]!;
      ctx.strokeStyle = rgba(GLOW, 0.65 * breath);
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.arc(root.x, root.y, 17 * root.depth, -0.4, Math.PI * 1.55);
      ctx.stroke();

      // Capability nodes at the edges, with Devanagari subscripts.
      const readyIds = state.current.ready;
      const capPoints = CAPABILITIES.map((_, i) =>
        project(capabilityPoint(i, time), yaw, pitch, scale, cx, cy),
      );
      CAPABILITIES.forEach((cap, i) => {
        const p = capPoints[i]!;
        const on = readyIds.length === 0 || readyIds.includes(cap.id);
        const flash = flashes.get(i) ?? -10;
        const lit = Math.max(0, 1 - (time - flash) / 1.2);
        ctx.shadowColor = rgba(lit > 0 ? AMBER : COPPER, 0.9);
        ctx.shadowBlur = 10 + lit * 30;
        ctx.strokeStyle = rgba(lit > 0 ? AMBER : COPPER, on ? 0.75 + lit * 0.25 : 0.25);
        ctx.lineWidth = 1.5;
        ctx.beginPath();
        const size = 13 * p.depth;
        for (let s = 0; s <= 6; s++) {
          const a = (s / 6) * Math.PI * 2 + Math.PI / 6;
          const px = p.x + Math.cos(a) * size;
          const py = p.y + Math.sin(a) * size;
          if (s === 0) ctx.moveTo(px, py);
          else ctx.lineTo(px, py);
        }
        ctx.stroke();
        ctx.shadowBlur = 0;
        ctx.fillStyle = rgba(lit > 0 ? AMBER : GLOW, on ? 0.9 : 0.35);
        ctx.beginPath();
        ctx.arc(p.x, p.y, 2.6 * p.depth, 0, Math.PI * 2);
        ctx.fill();
        if (lit > 0) {
          ctx.strokeStyle = rgba(AMBER, lit * 0.6);
          ctx.beginPath();
          ctx.arc(p.x, p.y, size + (1 - lit) * 26, 0, Math.PI * 2);
          ctx.stroke();
        }
        ctx.font = `600 11px 'JetBrains Mono', 'Cascadia Code', Consolas, monospace`;
        ctx.fillStyle = rgba(GLOW, on ? 0.85 : 0.35);
        ctx.fillText(cap.label.toUpperCase(), p.x, p.y + size + 16);
        ctx.font = `12px 'Nirmala UI', 'Noto Sans Devanagari', sans-serif`;
        ctx.fillStyle = rgba(COPPER, on ? 0.55 : 0.2);
        ctx.fillText(cap.deva, p.x, p.y + size + 31);
      });

      // Sutra threads: a glowing thread from the root, through the hub, to a capability.
      const interval = working ? 0.9 : 2.6;
      if (!reduced && time > 4 && time - lastThread > interval) {
        lastThread = time;
        spawnThread(time);
      }
      for (let k = threads.length - 1; k >= 0; k--) {
        const thread = threads[k]!;
        const progress = (time - thread.start) / 1.15;
        if (progress > 1.6) {
          threads.splice(k, 1);
          continue;
        }
        const points = thread.path.map((pt) => project(pt, yaw, pitch, scale, cx, cy));
        points[points.length - 1] = capPoints[thread.target]!;
        const head = Math.min(points.length - 1, Math.floor(progress * points.length));
        const tail = Math.max(0, head - 9);
        const fade = progress > 1 ? Math.max(0, 1 - (progress - 1) / 0.6) : 1;
        for (let s = tail; s < head; s++) {
          const a = points[s]!;
          const b = points[s + 1]!;
          const w = (s - tail) / Math.max(1, head - tail);
          ctx.strokeStyle = rgba(working ? AMBER : GLOW, (0.15 + w * 0.8) * fade);
          ctx.lineWidth = 1 + w * 2.2;
          ctx.beginPath();
          ctx.moveTo(a.x, a.y);
          ctx.lineTo(b.x, b.y);
          ctx.stroke();
        }
        const h = points[head]!;
        ctx.shadowColor = rgba(AMBER, 1);
        ctx.shadowBlur = 22 * fade;
        ctx.fillStyle = rgba(AMBER, 0.95 * fade);
        ctx.beginPath();
        ctx.arc(h.x, h.y, 3.2, 0, Math.PI * 2);
        ctx.fill();
        ctx.shadowBlur = 0;
        if (progress >= 1 && !thread.flashed) {
          thread.flashed = true;
          flashes.set(thread.target, time);
        }
      }
      ctx.globalCompositeOperation = 'source-over';
    }

    function loop(now: number) {
      if (visible && !document.hidden) draw(now);
      if (!reduced) frame = requestAnimationFrame(loop);
    }

    const observer = new ResizeObserver(() => {
      resize();
      if (reduced) draw(performance.now());
    });
    observer.observe(canvas);
    const intersection = new IntersectionObserver(([entry]) => (visible = !!entry?.isIntersecting));
    intersection.observe(canvas);
    const onPointer = (event: PointerEvent) => {
      const rect = canvas.getBoundingClientRect();
      pointerX = ((event.clientX - rect.left) / rect.width - 0.5) * 2;
      pointerY = ((event.clientY - rect.top) / rect.height - 0.5) * 2;
    };
    window.addEventListener('pointermove', onPointer, { passive: true });
    resize();
    frame = requestAnimationFrame(loop);
    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
      intersection.disconnect();
      window.removeEventListener('pointermove', onPointer);
    };
  }, []);

  return (
    <canvas
      ref={canvasRef}
      className={className}
      role="img"
      aria-label="PANINI Genesis: the Algorithmic P deriving itself from its root node and delegating to Search, Browser, Desktop and Code"
    />
  );
}
