import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdtemp, mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import afterPackModule from '../../scripts/panini-after-pack.cjs';

const afterPack = afterPackModule.default;

test('afterPack hashes copied bytes, not unsigned staging bytes', async () => {
  const root = await mkdtemp(join(tmpdir(), 'panini-after-pack-'));
  const runtime = join(root, 'resources', 'runtime');
  try {
    await mkdir(join(runtime, 'cli'), { recursive: true });
    const file = join(runtime, 'cli', 'PANINI-CLI.exe');
    await writeFile(file, 'signed-copy');
    await writeFile(
      join(runtime, 'release.json'),
      JSON.stringify({
        product: 'PANINI',
        version: '0.1.0',
        signatureStatus: 'requires-release-verification',
        files: [{ path: 'cli/PANINI-CLI.exe', bytes: 8, sha256: 'old' }],
      }),
    );
    await afterPack({ electronPlatformName: 'win32', appOutDir: root });
    const manifest = JSON.parse(await readFile(join(runtime, 'release.json'), 'utf8'));
    assert.equal(manifest.files[0].bytes, 11);
    assert.equal(
      manifest.files[0].sha256,
      createHash('sha256').update('signed-copy').digest('hex'),
    );
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test('afterPack rejects traversal in a runtime manifest', async () => {
  const root = await mkdtemp(join(tmpdir(), 'panini-after-pack-'));
  const runtime = join(root, 'resources', 'runtime');
  try {
    await mkdir(runtime, { recursive: true });
    await writeFile(
      join(runtime, 'release.json'),
      JSON.stringify({
        product: 'PANINI',
        files: [{ path: '../outside.exe' }],
      }),
    );
    await assert.rejects(afterPack({ electronPlatformName: 'win32', appOutDir: root }), /Unsafe/);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
