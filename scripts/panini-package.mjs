/** Build a self-contained candidate. Production signing is an explicit release gate. */
import { spawn, spawnSync } from 'node:child_process';
import {
  mkdir,
  cp,
  copyFile,
  writeFile,
  readFile,
  readdir,
  stat,
  rm,
  realpath,
} from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { build } from 'esbuild';
import { resolve, join, dirname, relative } from 'node:path';
import { fileURLToPath } from 'node:url';
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
process.chdir(root);
const unsigned = process.argv.includes('--unsigned');
const gitCommit = spawnSync('git', ['rev-parse', '--verify', 'HEAD'], {
  cwd: root,
  encoding: 'utf8',
  windowsHide: true,
});
const sourceCommit = gitCommit.status === 0 ? gitCommit.stdout.trim() : null;
const gitStatus = spawnSync('git', ['status', '--porcelain', '--untracked-files=all'], {
  cwd: root,
  encoding: 'utf8',
  windowsHide: true,
});
const sourceDirty = gitStatus.status !== 0 || Boolean(gitStatus.stdout.trim());
if (!unsigned && (!sourceCommit || sourceDirty))
  throw new Error('A signed release requires a committed, clean Git source state.');
const shellManifest = JSON.parse(
  await readFile(join(root, 'panini-core/apps/shell/package.json'), 'utf8'),
);
const installedElectron = (
  await readFile(join(root, 'node_modules/electron/dist/version'), 'utf8')
).trim();
if (installedElectron !== shellManifest.devDependencies.electron)
  throw new Error(
    'The local Electron distribution does not match the pinned shell version. Run npm ci.',
  );
if (!unsigned && !process.env.CSC_LINK && !process.env.WIN_CSC_LINK)
  throw new Error(
    'A release requires CSC_LINK or WIN_CSC_LINK and its signing credential. Use --unsigned for a local candidate.',
  );
if (!unsigned && !/^[a-f0-9]{40}$/i.test(process.env.PANINI_RELEASE_PUBLISHER_THUMBPRINT || ''))
  throw new Error('A release requires PANINI_RELEASE_PUBLISHER_THUMBPRINT for publisher pinning.');
const runtime = join(root, 'artifacts/panini-runtime');
await mkdir(join(root, 'artifacts'), { recursive: true });
const artifactRoot = await realpath(join(root, 'artifacts'));
if (
  artifactRoot !== join(await realpath(root), 'artifacts') ||
  resolve(runtime) !== join(artifactRoot, 'panini-runtime')
)
  throw new Error('Refusing to clean a runtime staging path outside this workspace.');
await rm(runtime, { recursive: true, force: true });
await mkdir(runtime, { recursive: true });
async function run(command, args, cwd = root, extraEnv = {}, expectedOutput = null) {
  await new Promise((resolve, reject) => {
    let captured = '';
    const child = spawn(command, args, {
      cwd,
      windowsHide: true,
      stdio: expectedOutput ? ['ignore', 'pipe', 'pipe'] : 'inherit',
      env: {
        ...process.env,
        DOTNET_CLI_TELEMETRY_OPTOUT: '1',
        CSC_IDENTITY_AUTO_DISCOVERY: unsigned ? 'false' : 'true',
        ...extraEnv,
      },
    });
    if (expectedOutput) {
      for (const stream of [child.stdout, child.stderr])
        stream.on('data', (data) => {
          const chunk = data.toString('utf8');
          captured = (captured + chunk).slice(-1_000_000);
          (stream === child.stdout ? process.stdout : process.stderr).write(chunk);
        });
    }
    child.on('error', reject);
    child.on('exit', (code) =>
      code === 0 && (!expectedOutput || captured.includes(expectedOutput))
        ? resolve()
        : reject(new Error(`${command} failed its packaged smoke check (exit ${code})`)),
    );
  });
}
await run(process.execPath, ['scripts/panini-build.mjs']);
await run(join(root, '.venv/Scripts/python.exe'), ['scripts/build-panini-cli-icon.py']);
await run(join(root, '.venv/Scripts/python.exe'), [
  '-m',
  'PyInstaller',
  '--noconfirm',
  '--distpath',
  join(root, 'artifacts/panini-frozen'),
  '--workpath',
  join(root, 'artifacts/panini-freeze-work'),
  'scripts/panini-backend.spec',
]);
await cp(join(root, 'artifacts/panini-frozen/Panini.Backend'), join(runtime, 'backend'), {
  recursive: true,
});
await run(join(root, '.venv/Scripts/python.exe'), [
  '-m',
  'PyInstaller',
  '--noconfirm',
  '--distpath',
  join(root, 'artifacts/panini-cli'),
  '--workpath',
  join(root, 'artifacts/panini-cli-work'),
  'scripts/panini-cli.spec',
]);
await mkdir(join(runtime, 'cli'), { recursive: true });
await copyFile(
  join(root, 'artifacts/panini-cli/PANINI-CLI.exe'),
  join(runtime, 'cli/PANINI-CLI.exe'),
);
// A frozen backend must run the first-party sandbox without Docker/Codex.
// This also catches missing dynamic Python imports such as flatbuffers.
if (process.platform === 'win32') {
  // The frozen smoke must not inherit developer-installed Docker, Codex, or pwsh.
  const systemRoot = process.env.SystemRoot;
  if (!systemRoot) throw new Error('SystemRoot is required for the Windows packaged smoke.');
  const pathKey = Object.keys(process.env).find((key) => key.toLowerCase() === 'path') || 'PATH';
  const systemOnlyPath = [
    join(systemRoot, 'System32'),
    systemRoot,
    join(systemRoot, 'System32/WindowsPowerShell/v1.0'),
  ].join(';');
  const isolatedPath = { [pathKey]: systemOnlyPath };
  await run(
    join(runtime, 'backend/Panini.Backend.exe'),
    ['--check-runtime'],
    runtime,
    isolatedPath,
    'PANINI_BACKEND_RUNTIME_IMPORTS_OK',
  );
  await run(
    join(runtime, 'backend/Panini.Backend.exe'),
    [
      'panini-cli',
      '-C',
      join(root, 'panini-core/capabilities/cli/tests/fixtures'),
      '--sandbox',
      'native',
      '--approve-all',
      'exec',
      'Run the packaged native sandbox smoke test',
    ],
    root,
    {
      ...isolatedPath,
      PANINI_CLI_SCRIPTED_MODEL: join(
        root,
        'panini-core/capabilities/cli/tests/fixtures/native_packaged_smoke.json',
      ),
      PANINI_CLI_DATA: join(root, 'artifacts/panini-sandbox-smoke'),
    },
    'PANINI_NATIVE_PACKAGED_SMOKE\r\n  exit 0',
  );
  await run(
    join(runtime, 'cli/PANINI-CLI.exe'),
    [
      '-C',
      join(root, 'panini-core/capabilities/cli/tests/fixtures'),
      '--sandbox',
      'native',
      '--approve-all',
      'exec',
      'Run the standalone native sandbox smoke test',
    ],
    root,
    {
      ...isolatedPath,
      PANINI_CLI_SCRIPTED_MODEL: join(
        root,
        'panini-core/capabilities/cli/tests/fixtures/native_packaged_smoke.json',
      ),
      PANINI_CLI_DATA: join(root, 'artifacts/panini-sandbox-smoke'),
    },
    'PANINI_NATIVE_PACKAGED_SMOKE\r\n  exit 0',
  );
  await run(join(root, '.venv/Scripts/python.exe'), [
    'scripts/panini-packaged-link-smoke.py',
    join(runtime, 'cli/PANINI-CLI.exe'),
  ]);
  await run(join(root, '.venv/Scripts/python.exe'), [
    'scripts/panini-packaged-dll-smoke.py',
    join(runtime, 'cli/PANINI-CLI.exe'),
  ]);
}
for (const [project, folder] of [
  ['Panini.RuntimeHost', 'host'],
  ['Panini.Desktop.Broker', 'broker'],
  ['Panini.Desktop.Worker', 'worker'],
  ['Panini.Desktop.Assistant', 'assistant'],
]) {
  await run('dotnet', [
    'publish',
    `panini-core/capabilities/desktop/${project}/${project}.csproj`,
    '-c',
    'Release',
    '-r',
    'win-x64',
    '--self-contained',
    'true',
    '-o',
    join(runtime, 'desktop', folder),
  ]);
}
await mkdir(join(runtime, 'node'), { recursive: true });
await copyFile(process.execPath, join(runtime, 'node/node.exe'));
await mkdir(join(runtime, 'scripts'), { recursive: true });
// The same runtime as `npm start`, bundled. ESM keeps its top-level await.
await build({
  absWorkingDir: root,
  entryPoints: ['frontend/server/main.mjs'],
  bundle: true,
  platform: 'node',
  format: 'esm',
  outfile: join(runtime, 'scripts/panini-runtime.mjs'),
  external: ['vite'],
  banner: {
    js: 'import { createRequire as paniniCreateRequire } from "node:module"; const require = paniniCreateRequire(import.meta.url);',
  },
});
await cp(join(root, 'frontend/public'), join(runtime, 'frontend/public'), { recursive: true });
// The web console's search pages run from the production build inside the runtime.
await run(
  process.execPath,
  [join(root, 'node_modules/vite/bin/vite.js'), 'build'],
  join(root, 'panini-core/capabilities/search'),
);
await cp(
  join(root, 'panini-core/capabilities/search/dist'),
  join(runtime, 'panini-core/capabilities/search/dist'),
  {
    recursive: true,
  },
);
await build({
  absWorkingDir: root,
  entryPoints: ['frontend/jev_sidecar.mjs'],
  bundle: true,
  platform: 'node',
  format: 'cjs',
  outfile: join(runtime, 'frontend/jev_sidecar.cjs'),
});
await cp(
  join(root, 'panini-core/capabilities/search/dist-worker'),
  join(runtime, 'panini-core/capabilities/search/dist-worker'),
  {
    recursive: true,
  },
);
// V2-08: the Playwright browser driver (the MCP server inside playwright-core). It drives the installed
// Edge or Chrome, so no browser download is bundled. Capability manifests ship beside it.
const driver = join(runtime, 'panini-core/capabilities/browser/playwright');
const playwrightCore = join(driver, 'node_modules/playwright-core');
await mkdir(playwrightCore, { recursive: true });
for (const name of ['package.json', 'init-overlay.js', 'tools-1.63.0.json'])
  await copyFile(
    join(root, 'panini-core/capabilities/browser/playwright', name),
    join(driver, name),
  );
for (const name of [
  'package.json',
  'browsers.json',
  'index.js',
  'LICENSE',
  'NOTICE',
  'ThirdPartyNotices.txt',
])
  await copyFile(join(root, 'node_modules/playwright-core', name), join(playwrightCore, name));
await cp(join(root, 'node_modules/playwright-core/lib'), join(playwrightCore, 'lib'), {
  recursive: true,
});
await cp(
  join(root, 'panini-core/capabilities/browser/manifests'),
  join(runtime, 'panini-core/capabilities/browser/manifests'),
  { recursive: true },
);
const licenses = join(runtime, 'licenses');
await mkdir(licenses, { recursive: true });
await mkdir(join(licenses, 'npm'), { recursive: true });
for (const [source, name] of [
  ['node_modules/playwright-core/LICENSE', 'playwright-core-LICENSE.txt'],
  ['node_modules/playwright-core/NOTICE', 'playwright-core-NOTICE.txt'],
])
  await copyFile(join(root, source), join(licenses, 'npm', name));
for (const [source, name] of [
  ['node_modules/electron/LICENSE', 'electron.txt'],
  ['node_modules/electron/dist/LICENSES.chromium.html', 'chromium.html'],
]) {
  try {
    await copyFile(join(root, source), join(licenses, name));
  } catch {}
}
// Hand-maintained notices are optional; generated package notices below are not.
try {
  await cp(join(root, 'licenses'), licenses, { recursive: true });
} catch (error) {
  if (error.code !== 'ENOENT') throw error;
}
await copyFile(join(root, 'THIRD_PARTY_NOTICES.md'), join(licenses, 'THIRD_PARTY_NOTICES.md'));
await copyFile(join(root, 'package-lock.json'), join(licenses, 'npm-dependencies.json'));
await copyFile(join(root, 'backend/requirements.txt'), join(licenses, 'python-dependencies.txt'));
await copyFile(
  join(root, 'backend/requirements-windows-lock.txt'),
  join(licenses, 'python-windows-lock.txt'),
);
await run(join(root, '.venv/Scripts/python.exe'), ['scripts/panini-licenses.py', licenses]);
const files = [];
async function inventory(path) {
  for (const entry of await readdir(path, { withFileTypes: true })) {
    const file = join(path, entry.name);
    if (entry.isDirectory()) await inventory(file);
    else {
      const content = await readFile(file);
      files.push({
        path: relative(runtime, file).replaceAll('\\', '/'),
        bytes: (await stat(file)).size,
        sha256: createHash('sha256').update(content).digest('hex'),
      });
    }
  }
}
await inventory(runtime);
await writeFile(
  join(runtime, 'release.json'),
  JSON.stringify(
    {
      product: 'PANINI',
      version: '0.1.0',
      signatureStatus: unsigned
        ? 'unsigned-development-candidate'
        : 'requires-release-verification',
      sourceCommit,
      sourceDirty,
      builtAt: new Date().toISOString(),
      files,
    },
    null,
    2,
  ),
);
await run(
  process.execPath,
  [
    join(root, 'node_modules/electron-builder/cli.js'),
    '--win',
    'nsis',
    '--publish',
    'never',
    `--config.electronDist=${join(root, 'node_modules/electron/dist')}`,
    ...(unsigned ? ['--config.win.signExecutable=false'] : ['--config.forceCodeSigning=true']),
  ],
  join(root, 'panini-core/apps/shell'),
);
await run(
  join(root, '.venv/Scripts/python.exe'),
  ['scripts/runtime-smoke.py', '--packaged'],
  root,
  {},
  'Unified runtime smoke passed.',
);
if (!unsigned) {
  await run('pwsh', [
    '-NoProfile',
    '-File',
    join(root, 'scripts/panini-release-gate.ps1'),
    '-BuildOnly',
  ]);
}
