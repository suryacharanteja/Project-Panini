/** The Algorithmic 'P' as a node network, plus its derivation order.
 *
 * Node 0 is the Root (Dhatu): the user's intent. The ring of the P is the derivation
 * network (Sutras); the stem descends to the gateways. Coordinates are in a unit space
 * (y down); z gives the network a shallow dome so it reads as 3D when it turns.
 */

export type Vec3 = { x: number; y: number; z: number };
export type Graph = { nodes: Vec3[]; edges: [number, number][]; order: number[]; hub: number };

function seeded(seed: number) {
  let s = seed >>> 0;
  return () => {
    s = (s * 1664525 + 1013904223) >>> 0;
    return s / 4294967296;
  };
}

export function buildP(seed = 7): Graph {
  const random = seeded(seed);
  const cx = 0.12;
  const cy = -0.3;
  const nodes: Vec3[] = [];
  const add = (x: number, y: number, z = 0) => nodes.push({ x, y, z }) - 1;

  const root = add(cx - 0.02, cy + 0.02, 0.12); // Dhatu, slightly forward.
  // Outer ring: the bowl of the P, open on the lower left where the stem joins.
  const outer: number[] = [];
  for (let deg = -165; deg <= 125; deg += 22.3) {
    const a = (deg * Math.PI) / 180;
    outer.push(add(cx + Math.cos(a) * 0.64, cy + Math.sin(a) * 0.64, Math.sin(a * 1.7) * 0.12));
  }
  // Middle ring.
  const middle: number[] = [];
  for (let deg = -150; deg <= 110; deg += 32.5) {
    const a = ((deg + 11) * Math.PI) / 180;
    middle.push(add(cx + Math.cos(a) * 0.4, cy + Math.sin(a) * 0.4, 0.05 + random() * 0.08));
  }
  // Inner spiral near the root: the question mark's hook.
  const inner: number[] = [];
  for (let deg = -60; deg <= 160; deg += 55) {
    const a = (deg * Math.PI) / 180;
    inner.push(add(cx + Math.cos(a) * 0.2 + 0.03, cy + Math.sin(a) * 0.2, 0.1));
  }
  // The hub where the stem leaves the bowl, then the stem and its base triangle.
  const hub = add(cx - 0.3, cy + 0.26, 0.06);
  const stem = [
    add(cx - 0.43, cy + 0.6, 0.02),
    add(cx - 0.2, cy + 0.66, 0.04),
    add(cx - 0.33, cy + 0.86, 0.0),
    add(cx - 0.52, cy + 1.02, -0.04),
    add(cx - 0.16, cy + 1.02, -0.02),
  ];

  const edges = new Set<string>();
  const link = (a: number, b: number) => {
    if (a !== b) edges.add(a < b ? `${a}-${b}` : `${b}-${a}`);
  };
  outer.forEach((n, i) => i > 0 && link(outer[i - 1]!, n));
  middle.forEach((n, i) => i > 0 && link(middle[i - 1]!, n));
  // Triangulate rings by nearest neighbours.
  const near = (from: number, pool: number[], k: number) =>
    pool
      .map((n) => ({
        n,
        d: Math.hypot(nodes[n]!.x - nodes[from]!.x, nodes[n]!.y - nodes[from]!.y),
      }))
      .sort((a, b) => a.d - b.d)
      .slice(0, k)
      .map((v) => v.n);
  for (const n of outer) for (const m of near(n, middle, 2)) link(n, m);
  for (const n of middle) for (const m of near(n, [...inner, root], 1)) link(n, m);
  for (const n of inner) link(n, root);
  link(root, hub);
  link(hub, near(hub, middle, 1)[0]!);
  link(hub, stem[0]!);
  link(hub, stem[1]!);
  link(stem[0]!, stem[1]!);
  link(stem[0]!, stem[2]!);
  link(stem[1]!, stem[2]!);
  link(stem[2]!, stem[3]!);
  link(stem[2]!, stem[4]!);
  link(stem[0]!, stem[3]!);
  link(stem[3]!, stem[4]!);
  link(outer[outer.length - 1]!, middle[middle.length - 1]!);

  const edgeList = [...edges].map((e) => e.split('-').map(Number) as [number, number]);
  // Derivation order: breadth-first from the root, as rules apply outward from the Dhatu.
  const adjacency = nodes.map(() => [] as number[]);
  for (const [a, b] of edgeList) {
    adjacency[a]!.push(b);
    adjacency[b]!.push(a);
  }
  const order: number[] = [root];
  const seen = new Set(order);
  for (let i = 0; i < order.length; i++) {
    for (const next of adjacency[order[i]!]!) {
      if (!seen.has(next)) {
        seen.add(next);
        order.push(next);
      }
    }
  }
  for (let i = 0; i < nodes.length; i++) if (!seen.has(i)) order.push(i);
  return { nodes, edges: edgeList, order, hub };
}

/** Rotate around Y then X, then perspective-project into screen space. */
export function project(
  p: Vec3,
  yaw: number,
  pitch: number,
  scale: number,
  cx: number,
  cy: number,
) {
  const cosY = Math.cos(yaw);
  const sinY = Math.sin(yaw);
  const x1 = p.x * cosY + p.z * sinY;
  const z1 = -p.x * sinY + p.z * cosY;
  const cosX = Math.cos(pitch);
  const sinX = Math.sin(pitch);
  const y2 = p.y * cosX - z1 * sinX;
  const z2 = p.y * sinX + z1 * cosX;
  const depth = 3.2 / (3.2 - z2);
  return { x: cx + x1 * scale * depth, y: cy + y2 * scale * depth, depth, z: z2 };
}

/** Maheshvara (Śiva) sūtras: the opening rules that organise all phonemes. */
export const SHIVA_SUTRAS =
  'अइउण् ऋऌक् एओङ् ऐऔच् हयवरट् लण् ञमङणनम् झभञ् घढधष् जबगडदश् खफछठथचटतव् कपय् शषसर् हल्';

export const CAPABILITIES = [
  { id: 'search', label: 'Search', deva: 'अन्वेषण', angle: -2.4 },
  { id: 'browser', label: 'Browser', deva: 'जाल', angle: 2.35 },
  { id: 'desktop', label: 'Desktop', deva: 'पटल', angle: 0.75 },
  { id: 'code', label: 'Code', deva: 'सूत्र', angle: -0.72 },
] as const;
