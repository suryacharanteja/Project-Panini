/** Pin SSR code and its hashed browser assets to the same release for this process. */
import { cpSync, existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

export function snapshotRelease(root, { immutable = false } = {}) {
  const build = join(root, 'panini-core/capabilities/search/dist');
  if (!existsSync(join(build, 'server/server.js')) || !existsSync(join(build, 'client/assets')))
    throw new Error('Search build is missing. Run npm run build first.');
  // An installed PANINI runtime cannot change underneath itself; use it in place.
  if (immutable)
    return {
      entry: pathToFileURL(join(build, 'server/server.js')).href,
      clientRoot: join(build, 'client'),
      publicRoot: join(root, 'frontend/public'),
      dispose() {},
    };
  const runtime = join(root, 'artifacts/runtime');
  mkdirSync(runtime, { recursive: true });
  const directory = mkdtempSync(join(runtime, 'release-'));
  const dispose = () => rmSync(directory, { recursive: true, force: true });
  try {
    cpSync(build, directory, { recursive: true });
    writeFileSync(join(directory, 'package.json'), '{"type":"module"}\n');
    cpSync(join(root, 'frontend/public'), join(directory, 'frontend'), { recursive: true });
    return {
      entry: pathToFileURL(join(directory, 'server/server.js')).href,
      clientRoot: join(directory, 'client'),
      publicRoot: join(directory, 'frontend'),
      dispose,
    };
  } catch (error) {
    dispose();
    throw error;
  }
}
