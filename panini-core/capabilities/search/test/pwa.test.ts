import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

function pngSize(path: string) {
  const buf = readFileSync(path);
  expect(buf.subarray(1, 4).toString('ascii')).toBe('PNG');
  return { width: buf.readUInt32BE(16), height: buf.readUInt32BE(20) };
}

describe('installable app manifest', () => {
  const manifest = JSON.parse(readFileSync('public/manifest.webmanifest', 'utf8')) as {
    name: string;
    short_name: string;
    start_url: string;
    scope: string;
    display: string;
    icons: Array<{ src: string; sizes: string; type: string; purpose?: string }>;
    share_target: { action: string; method: string; enctype: string; params: { text: string } };
    shortcuts: Array<{ name: string; url: string }>;
  };

  it('has the fields Chrome needs to offer install', () => {
    expect(manifest.name).toBe('JevRev Search');
    expect(manifest.short_name).toBe('JevRev Search');
    expect(manifest.start_url).toBe('/search/');
    expect(manifest.scope).toBe('/search/');
    expect(manifest.display).toBe('standalone');
    const sizes = new Set(manifest.icons.map((icon) => icon.sizes));
    expect(sizes.has('192x192')).toBe(true);
    expect(sizes.has('512x512')).toBe(true);
    expect(manifest.icons.some((icon) => icon.purpose === 'maskable')).toBe(true);
  });

  it('opens shared text as a search, not as a document cache', () => {
    expect(manifest.share_target).toEqual({
      action: '/search/results',
      method: 'GET',
      enctype: 'application/x-www-form-urlencoded',
      params: { text: 'q' },
    });
    expect(manifest.shortcuts.some((shortcut) => shortcut.url === '/search/')).toBe(true);
  });

  it.each([
    ['public/icon-192.png', 192],
    ['public/icon-512.png', 512],
    ['public/icon-maskable-512.png', 512],
  ] as const)('%s is %d px', (path, size) => {
    expect(pngSize(path)).toEqual({ width: size, height: size });
  });
});

describe('service worker', () => {
  const sw = readFileSync('public/sw.js', 'utf8');

  it('is installable without caching searches', () => {
    expect(sw).toContain("url.pathname.startsWith('/search/api/')");
    expect(sw).toContain("request.mode !== 'navigate'");
    expect(sw).toContain("caches.match('/search/offline.html')");
    expect(sw).toContain('skipWaiting');
    expect(sw).not.toContain('/api/ask');
  });
});
