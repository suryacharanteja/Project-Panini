/** Per-user discovery file so `npm run console` can open the running runtime's console. */
import { chmodSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';

export function dataDirectory(env = process.env) {
  if (env.PANINI_DATA_DIR) return env.PANINI_DATA_DIR;
  if (process.platform === 'win32')
    return join(env.LOCALAPPDATA || join(homedir(), 'AppData/Local'), 'PANINI');
  return join(homedir(), '.local/share/panini');
}

export function runtimeFile(env = process.env) {
  const path = join(dataDirectory(env), 'runtime.json');
  return {
    path,
    write(value) {
      mkdirSync(dataDirectory(env), { recursive: true });
      writeFileSync(path, JSON.stringify(value), { mode: 0o600 });
      if (process.platform !== 'win32') chmodSync(path, 0o600);
    },
    read() {
      try {
        return JSON.parse(readFileSync(path, 'utf8'));
      } catch {
        return null;
      }
    },
    remove() {
      rmSync(path, { force: true });
    },
  };
}
