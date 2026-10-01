import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { mkdtemp, mkdir, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import test from 'node:test';

const gate = resolve('scripts/panini-release-gate.ps1');
const installedIntegrity = resolve('scripts/panini-installed-runtime-integrity.ps1');

function runGate(packageRoot, runtime, extraArgs = []) {
  try {
    return execFileSync(
      'pwsh',
      [
        '-NoProfile',
        '-File',
        gate,
        '-PackageRoot',
        packageRoot,
        '-RuntimeRoot',
        runtime,
        ...extraArgs,
      ],
      {
        encoding: 'utf8',
        stdio: ['ignore', 'pipe', 'pipe'],
        env: { ...process.env, PANINI_RELEASE_PUBLISHER_THUMBPRINT: 'A'.repeat(40) },
      },
    );
  } catch (error) {
    return `${error.stdout || ''}${error.stderr || ''}`;
  }
}

test(
  'release gate checks hashes and unlisted files before rejecting unsigned builds',
  {
    skip: process.platform !== 'win32',
  },
  async () => {
    const root = await mkdtemp(join(tmpdir(), 'panini-release-gate-'));
    const runtime = join(root, 'win-unpacked', 'resources', 'runtime');
    try {
      await mkdir(join(runtime, 'cli'), { recursive: true });
      await mkdir(join(runtime, 'backend'), { recursive: true });
      await mkdir(join(runtime, 'desktop', 'host'), { recursive: true });
      await writeFile(join(root, 'PANINI Setup 0.1.0.exe'), 'installer');
      await writeFile(join(runtime, 'cli', 'PANINI-CLI.exe'), 'binary');
      await writeFile(join(runtime, 'backend', 'Panini.Backend.exe'), 'binary');
      await writeFile(join(runtime, 'desktop', 'host', 'Panini.RuntimeHost.exe'), 'binary');
      const sha256 = createHash('sha256').update('binary').digest('hex');
      const manifest = {
        product: 'PANINI',
        signatureStatus: 'unsigned-development-candidate',
        sourceCommit: 'a'.repeat(40),
        sourceDirty: false,
        files: [
          'cli/PANINI-CLI.exe',
          'backend/Panini.Backend.exe',
          'desktop/host/Panini.RuntimeHost.exe',
        ].map((path) => ({ path, bytes: 6, sha256 })),
      };
      await writeFile(join(runtime, 'release.json'), JSON.stringify(manifest));
      assert.match(runGate(root, runtime), /Verified 3 packaged runtime file hashes/);
      const unexpected = join(runtime, 'unexpected.txt');
      await writeFile(unexpected, 'surprise');
      assert.match(runGate(root, runtime), /Unlisted runtime file/);
      await rm(unexpected);
      manifest.signatureStatus = 'requires-release-verification';
      manifest.sourceDirty = true;
      await writeFile(join(runtime, 'release.json'), JSON.stringify(manifest));
      assert.match(runGate(root, runtime), /clean Git source commit/);
      manifest.sourceDirty = false;
      await writeFile(join(runtime, 'release.json'), JSON.stringify(manifest));
      assert.match(runGate(root, runtime), /requires PANINI_RELEASE_QUALIFICATION_EVIDENCE/);
      const evidencePath = join(root, 'qualification.json');
      const evidence = {
        product: 'PANINI',
        kind: 'clean-vm-functional-qualification',
        status: 'passed',
        installerSha256: 'incorrect',
        checks: {},
      };
      await writeFile(evidencePath, JSON.stringify(evidence));
      assert.match(
        runGate(root, runtime, ['-QualificationEvidence', evidencePath]),
        /does not match this exact installer/,
      );
      evidence.installerSha256 = createHash('sha256').update('installer').digest('hex');
      await writeFile(evidencePath, JSON.stringify(evidence));
      assert.match(
        runGate(root, runtime, ['-QualificationEvidence', evidencePath]),
        /lacks a signed Windows 11 qualification run/,
      );
      Object.assign(evidence, {
        windowsBuild: 26200,
        installerSignature: 'Valid',
        runId: 'test-run',
        startedAt: '2026-09-27T00:00:00Z',
        finishedAt: '2026-09-27T00:01:00Z',
        checks: Object.fromEntries(
          [
            'noExternalSandboxDependency',
            'installAndShortcut',
            'cliLaunchAndKeyOnboarding',
            'credentialRoundTrip',
            'repairInstall',
            'installedNativeSandbox',
            'installedHardLinkBoundary',
            'installedSystem32DllBoundary',
            'uninstall',
          ].map((name) => [name, 'passed']),
        ),
      });
      await writeFile(evidencePath, JSON.stringify(evidence));
      assert.match(
        runGate(root, runtime, ['-QualificationEvidence', evidencePath]),
        /missing: installedRuntimeIntegrity/,
      );
      assert.match(runGate(root, runtime, ['-BuildOnly']), /Invalid signature/);
    } finally {
      await rm(root, { recursive: true, force: true });
    }
  },
);

test(
  'installed runtime verifier detects changed and unlisted files',
  { skip: process.platform !== 'win32' },
  async () => {
    const root = await mkdtemp(join(tmpdir(), 'panini-installed-integrity-'));
    try {
      for (const name of [
        'cli/PANINI-CLI.exe',
        'backend/Panini.Backend.exe',
        'desktop/host/Panini.RuntimeHost.exe',
      ]) {
        const path = join(root, name);
        await mkdir(join(path, '..'), { recursive: true });
        await writeFile(path, 'binary');
      }
      const sha256 = createHash('sha256').update('binary').digest('hex');
      const manifest = {
        product: 'PANINI',
        files: [
          'cli/PANINI-CLI.exe',
          'backend/Panini.Backend.exe',
          'desktop/host/Panini.RuntimeHost.exe',
        ].map((path) => ({ path, bytes: 6, sha256 })),
      };
      await writeFile(join(root, 'release.json'), JSON.stringify(manifest));
      const verify = () => runInstalledIntegrity(root);
      assert.match(verify(), /Verified 3 installed runtime file hashes/);
      await writeFile(join(root, 'cli', 'PANINI-CLI.exe'), 'tampered');
      assert.match(verify(), /Installed runtime size mismatch/);
      await writeFile(join(root, 'cli', 'PANINI-CLI.exe'), 'binary');
      await writeFile(join(root, 'extra.txt'), 'unlisted');
      assert.match(verify(), /Unlisted installed runtime file/);
    } finally {
      await rm(root, { recursive: true, force: true });
    }
  },
);

function runInstalledIntegrity(runtimeRoot) {
  try {
    return execFileSync(
      'pwsh',
      ['-NoProfile', '-File', installedIntegrity, '-RuntimeRoot', runtimeRoot],
      {
        encoding: 'utf8',
        stdio: ['ignore', 'pipe', 'pipe'],
      },
    );
  } catch (error) {
    return `${error.stdout || ''}${error.stderr || ''}`;
  }
}
