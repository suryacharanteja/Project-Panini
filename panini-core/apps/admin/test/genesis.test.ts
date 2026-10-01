import { describe, expect, it } from 'vitest';
import { buildP, CAPABILITIES, project, SHIVA_SUTRAS } from '../src/genesis/model';

describe('the Algorithmic P', () => {
  const graph = buildP();

  it('derives every node from the root, breadth first', () => {
    expect(graph.order[0]).toBe(0); // The Dhatu comes first.
    expect(new Set(graph.order).size).toBe(graph.nodes.length);
    expect(graph.order).toHaveLength(graph.nodes.length);
  });

  it('is one connected network', () => {
    const adjacency = graph.nodes.map(() => new Set<number>());
    for (const [a, b] of graph.edges) {
      adjacency[a]!.add(b);
      adjacency[b]!.add(a);
    }
    const seen = new Set([0]);
    const queue = [0];
    while (queue.length)
      for (const next of adjacency[queue.shift()!]!)
        if (!seen.has(next)) (seen.add(next), queue.push(next));
    expect(seen.size).toBe(graph.nodes.length);
    expect(graph.edges.every(([a, b]) => a !== b)).toBe(true);
  });

  it('is deterministic, so the emblem is the same on every load', () => {
    expect(buildP()).toEqual(graph);
  });

  it('projects with perspective and rotation', () => {
    const centre = project({ x: 0, y: 0, z: 0 }, 0, 0, 100, 50, 50);
    expect(centre).toMatchObject({ x: 50, y: 50, depth: 1 });
    const near = project({ x: 1, y: 0, z: 1 }, 0, 0, 100, 0, 0);
    const far = project({ x: 1, y: 0, z: -1 }, 0, 0, 100, 0, 0);
    expect(near.x).toBeGreaterThan(far.x); // Closer points appear larger.
    const turned = project({ x: 1, y: 0, z: 0 }, Math.PI / 2, 0, 100, 0, 0);
    expect(Math.abs(turned.x)).toBeLessThan(1e-6);
  });

  it('names the four capabilities with their Devanagari subscripts', () => {
    expect(CAPABILITIES.map((c) => c.id)).toEqual(['search', 'browser', 'desktop', 'code']);
    expect(CAPABILITIES.find((c) => c.id === 'search')!.deva).toBe('अन्वेषण');
    expect(SHIVA_SUTRAS.startsWith('अइउण्')).toBe(true);
  });
});
