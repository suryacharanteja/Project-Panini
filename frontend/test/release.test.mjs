import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync, rmSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { once } from 'node:events';
import { snapshotRelease } from '../server/release.mjs';
import { createGateway } from '../server/gateway.mjs';

test('a rebuild cannot remove the CSS and JS used by an already running release', async () => {
  const root = mkdtempSync(join(tmpdir(), 'jev-release-test-'));
  let release, gateway;
  try {
    const build = join(root, 'panini-core/capabilities/search/dist');
    mkdirSync(join(build, 'server'), { recursive: true });
    mkdirSync(join(build, 'client/assets'), { recursive: true });
    mkdirSync(join(root, 'frontend/public'), { recursive: true });
    writeFileSync(join(build, 'client/assets/styles-old.css'), 'body { color: red; }');
    writeFileSync(join(build, 'client/assets/index-old.js'), 'globalThis.loaded = true;');
    writeFileSync(
      join(build, 'server/server.js'),
      `export default {fetch: () => new Response('<link href="/search/assets/styles-old.css"><script src="/search/assets/index-old.js"></script>')};`,
    );
    release = snapshotRelease(root);
    // Vite empties dist when publishing another build.
    rmSync(build, { recursive: true });
    mkdirSync(join(build, 'client/assets'), { recursive: true });
    writeFileSync(join(build, 'client/assets/styles-new.css'), 'body { color: blue; }');
    gateway = createGateway({
      production: true,
      backendUrl: 'http://127.0.0.1:1',
      searchFetch: (await import(release.entry)).default.fetch,
      searchClientRoot: release.clientRoot,
      publicRoot: release.publicRoot,
    });
    gateway.server.listen(0, '127.0.0.1');
    await once(gateway.server, 'listening');
    const base = `http://127.0.0.1:${gateway.server.address().port}`;
    const html = await (await fetch(`${base}/search/results?q=test`)).text();
    for (const [, path] of html.matchAll(/(?:href|src)="([^"]+)"/g)) {
      const response = await fetch(base + path);
      assert.equal(response.status, 200);
      assert.match(
        response.headers.get('content-type'),
        path.endsWith('.css') ? /text\/css/ : /javascript/,
      );
    }
    const missing = await fetch(`${base}/search/assets/missing.js`);
    assert.equal(missing.status, 404);
    assert.match(missing.headers.get('content-type'), /application\/json/);
    release.dispose();
    assert.equal(existsSync(release.clientRoot), false);
  } finally {
    await gateway?.close();
    release?.dispose();
    rmSync(root, { recursive: true, force: true });
  }
});
