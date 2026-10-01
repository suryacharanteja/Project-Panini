import { build } from 'esbuild';
import { build as viteBuild } from 'vite';
import { mkdir, copyFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
process.chdir(root);
await build({
  absWorkingDir: root,
  entryPoints: ['src/main/main.ts'],
  bundle: true,
  platform: 'node',
  format: 'cjs',
  outfile: 'dist/main/main.cjs',
  external: ['electron'],
  sourcemap: true,
});
await build({
  absWorkingDir: root,
  entryPoints: ['src/main/preload.ts'],
  bundle: true,
  platform: 'node',
  format: 'cjs',
  outfile: 'dist/main/preload.cjs',
  external: ['electron'],
});
await viteBuild({ configFile: resolve(root, 'vite.config.ts') });
await mkdir('dist/renderer/brand', { recursive: true });
await copyFile('../../../frontend/public/favicon.ico', 'dist/renderer/brand/favicon.ico');
// PANINI brand icons (window, taskbar, tray) from the brand package.
await copyFile('../../../assets/panini-icon.ico', 'dist/renderer/brand/panini-icon.ico');
await copyFile('../../../assets/panini-tray-32.png', 'dist/renderer/brand/panini-tray-32.png');
await copyFile('../../../assets/panini-cli.ico', 'dist/renderer/brand/panini-cli.ico');
await copyFile('../../../assets/panini-cli-64.png', 'dist/renderer/brand/panini-cli.png');
