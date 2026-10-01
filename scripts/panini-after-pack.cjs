/** Refresh the runtime manifest after electron-builder transforms copied executables. */
const { createHash } = require('node:crypto');
const { createReadStream } = require('node:fs');
const { readFile, writeFile, lstat } = require('node:fs/promises');
const { join, resolve, sep } = require('node:path');

exports.default = async function afterPack(context) {
  if (context.electronPlatformName !== 'win32') return;
  const runtime = resolve(context.appOutDir, 'resources', 'runtime');
  const manifestPath = join(runtime, 'release.json');
  const manifest = JSON.parse(await readFile(manifestPath, 'utf8'));
  if (manifest.product !== 'PANINI' || !Array.isArray(manifest.files) || !manifest.files.length)
    throw new Error('Invalid PANINI runtime manifest');
  const seen = new Set();
  for (const entry of manifest.files) {
    if (
      typeof entry.path !== 'string' ||
      entry.path.includes('\\') ||
      entry.path.includes('\0') ||
      entry.path.split('/').some((part) => !part || part === '.' || part === '..') ||
      entry.path.startsWith('/') ||
      seen.has(entry.path)
    )
      throw new Error(`Unsafe or duplicate runtime path: ${entry.path}`);
    seen.add(entry.path);
    const file = resolve(runtime, ...entry.path.split('/'));
    if (!file.startsWith(runtime + sep)) throw new Error(`Runtime path escaped: ${entry.path}`);
    const info = await lstat(file);
    if (!info.isFile() || info.isSymbolicLink())
      throw new Error(`Not a regular runtime file: ${entry.path}`);
    const hash = createHash('sha256');
    for await (const chunk of createReadStream(file)) hash.update(chunk);
    entry.bytes = info.size;
    entry.sha256 = hash.digest('hex');
  }
  await writeFile(manifestPath, JSON.stringify(manifest, null, 2));
  console.log(`PANINI post-copy manifest refreshed: ${manifest.files.length} files`);
};
