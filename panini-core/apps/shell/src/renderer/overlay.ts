// The full-screen delegation moment (opt-in): a sutra thread flies from the avatar to the
// screen edge of the capability PANINI delegates to, then fades. Click-through, never focused.
import { CAPABILITIES } from '../../../admin/src/genesis/model';

const params = new URLSearchParams(location.search);
const capability = params.get('capability') || 'search';
const fromX = Number(params.get('x') || 0);
const fromY = Number(params.get('y') || 0);
const canvas = document.getElementById('thread') as HTMLCanvasElement;
const ctx = canvas.getContext('2d')!;
document.body.style.cssText = 'margin:0;background:transparent;overflow:hidden';
canvas.style.cssText = 'position:fixed;inset:0;width:100vw;height:100vh';

const W = innerWidth;
const H = innerHeight;
const dpr = Math.min(devicePixelRatio || 1, 2);
canvas.width = W * dpr;
canvas.height = H * dpr;
ctx.scale(dpr, dpr);

// Capability anchors at the screen edges, as in the brand concept:
// Search top-left, Code top-right, Desktop bottom-right, Browser bottom-left.
const edges: Record<string, [number, number]> = {
  search: [W * 0.06, H * 0.12],
  code: [W * 0.94, H * 0.12],
  desktop: [W * 0.94, H * 0.88],
  browser: [W * 0.06, H * 0.88],
};
const targets = capability === 'agent' ? Object.keys(edges) : [capability];
const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
const start = performance.now();
const DURATION = reduced ? 900 : 1500;

function frame(now: number) {
  const t = Math.min(1, (now - start) / DURATION);
  ctx.clearRect(0, 0, W, H);
  ctx.globalCompositeOperation = 'lighter';
  for (const id of targets) {
    const [ex, ey] = edges[id] ?? edges.search!;
    const mx = (fromX + ex) / 2 + (ey - fromY) * 0.18;
    const my = (fromY + ey) / 2 - (ex - fromX) * 0.08;
    const drawn = reduced ? 1 : Math.min(1, t / 0.55);
    const fade = t < 0.7 ? 1 : 1 - (t - 0.7) / 0.3;
    const point = (u: number) => ({
      x: (1 - u) * (1 - u) * fromX + 2 * (1 - u) * u * mx + u * u * ex,
      y: (1 - u) * (1 - u) * fromY + 2 * (1 - u) * u * my + u * u * ey,
    });
    ctx.lineWidth = 2.2;
    ctx.shadowColor = 'rgba(255,191,0,0.9)';
    ctx.shadowBlur = 16;
    ctx.strokeStyle = `rgba(232,184,154,${0.75 * fade})`;
    ctx.beginPath();
    for (let k = 0; k <= 60; k++) {
      const p = point((k / 60) * drawn);
      if (k === 0) ctx.moveTo(p.x, p.y);
      else ctx.lineTo(p.x, p.y);
    }
    ctx.stroke();
    const head = point(drawn);
    ctx.fillStyle = `rgba(255,191,0,${fade})`;
    ctx.beginPath();
    ctx.arc(head.x, head.y, 4, 0, Math.PI * 2);
    ctx.fill();
    if (drawn >= 1) {
      const cap = CAPABILITIES.find((c) => c.id === id);
      const ring = (t - 0.55) / 0.45;
      ctx.strokeStyle = `rgba(255,191,0,${0.7 * fade})`;
      ctx.beginPath();
      ctx.arc(ex, ey, 14 + ring * 30, 0, Math.PI * 2);
      ctx.stroke();
      ctx.shadowBlur = 0;
      ctx.globalCompositeOperation = 'source-over';
      ctx.font = "600 12px 'JetBrains Mono', Consolas, monospace";
      ctx.textAlign = ex < W / 2 ? 'left' : 'right';
      ctx.fillStyle = `rgba(232,184,154,${fade})`;
      const tx = ex + (ex < W / 2 ? 26 : -26);
      ctx.fillText((cap?.label ?? id).toUpperCase(), tx, ey + 4);
      ctx.font = "13px 'Nirmala UI', 'Noto Sans Devanagari', sans-serif";
      ctx.fillStyle = `rgba(184,115,51,${0.8 * fade})`;
      ctx.fillText(cap?.deva ?? '', tx, ey + 22);
      ctx.globalCompositeOperation = 'lighter';
    }
  }
  if (t < 1) requestAnimationFrame(frame);
  else window.close();
}
requestAnimationFrame(frame);
