import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { HOME_CANONICAL, SEARCH_ROBOTS, SITE_ORIGIN, SITEMAP_URL } from '@/lib/seo';

describe('crawlable homepage only', () => {
  const robots = readFileSync('public/robots.txt', 'utf8');
  const sitemap = readFileSync('public/sitemap.xml', 'utf8');

  it('advertises a sitemap and does not hide /search from crawlers', () => {
    expect(robots).toContain(`Sitemap: ${SITEMAP_URL}`);
    expect(robots).toMatch(/^User-agent: \*$/m);
    expect(robots).toContain('Allow: /');
    expect(robots).toContain('Disallow: /api/');
    expect(robots).not.toMatch(/Disallow:\s*\/search/);
  });

  it('lists only the homepage', () => {
    const locs = [...sitemap.matchAll(/<loc>([^<]+)<\/loc>/g)].map((match) => match[1]);
    expect(locs).toEqual([HOME_CANONICAL]);
    expect(sitemap).not.toContain(`${SITE_ORIGIN}/search/results`);
    expect(sitemap).not.toContain('/api/');
  });

  it('wires those directives onto the routes', () => {
    const home = readFileSync('src/routes/index.tsx', 'utf8');
    const search = readFileSync('src/routes/results.tsx', 'utf8');
    expect(home).toContain("rel: 'canonical'");
    expect(home).toContain('HOME_CANONICAL');
    expect(search).toContain('SEARCH_ROBOTS');
    expect(search).toContain('HOME_CANONICAL');
  });
});
