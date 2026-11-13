// Real packaged Electron shell and Windows SAPI synthesis; no provider or model requests.
import assert from 'node:assert/strict';
import { _electron as electron } from 'playwright';
import { mkdir, mkdtemp, writeFile } from 'node:fs/promises';
import { createServer } from 'node:net';
import { resolve } from 'node:path';

const root = resolve(import.meta.dirname, '..');
const evidence = resolve(root, 'artifacts/local-tts');
await mkdir(evidence, { recursive: true });
const profile = await mkdtemp(resolve(evidence, 'profile-'));
const data = await mkdtemp(resolve(evidence, 'data-'));
async function port() {
  const server = createServer();
  await new Promise((done) => server.listen(0, '127.0.0.1', done));
  const value = server.address().port;
  await new Promise((done) => server.close(done));
  return value;
}
const ports = [];
while (ports.length < 3) {
  const candidate = await port();
  if (!ports.includes(candidate)) ports.push(candidate);
}
const env = {
  ...process.env,
  PANINI_DATA_DIR: data,
  PANINI_LOCAL_MODE: 'private',
  FRONTEND_PORT: String(ports[0]),
  BACKEND_PORT: String(ports[1]),
  SEARCH_PORT: String(ports[2]),
  PANINI_REMOTE_MODE: 'off',
  PANINI_REDTEAM_ENFORCE: 'false',
};
delete env.ELECTRON_RUN_AS_NODE;
const app = await electron.launch({
  executablePath: resolve(root, 'artifacts/panini-package/win-unpacked/PANINI.exe'),
  args: [`--user-data-dir=${profile}`],
  env,
  timeout: 90000,
});
try {
  const page = await app.firstWindow();
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.getByRole('button', { name: 'Read reply locally' }).waitFor({ timeout: 90000 });
  const spoken = await page.evaluate(() =>
    window.panini.invoke('synthesize-local', { text: 'PANINI local voice playback check.' }),
  );
  assert.equal(spoken.engine, 'windows-sapi-local');
  assert(Buffer.from(spoken.audio_b64, 'base64').subarray(0, 4).toString() === 'RIFF');
  await page.evaluate(() => {
    window.paniniTtsPlayCount = 0;
    const original = HTMLMediaElement.prototype.play;
    HTMLMediaElement.prototype.play = function (...args) {
      window.paniniTtsPlayCount++;
      return original.apply(this, args);
    };
  });
  await page.getByRole('button', { name: 'Read reply locally' }).click();
  await page.waitForFunction(() => window.paniniTtsPlayCount > 0, null, { timeout: 30000 });
  assert.equal(errors.length, 0, errors.join('\n'));
  await page.screenshot({ path: resolve(evidence, 'shell.png') });
  const result = {
    passed: true,
    packaged_shell: true,
    authenticated_ipc: true,
    wav_header: true,
    renderer_play_invoked: true,
    audible_speaker_output_qualified: false,
    physical_microphone_qualified: false,
  };
  await writeFile(resolve(evidence, 'shell-smoke.json'), JSON.stringify(result, null, 2) + '\n');
  console.log('PANINI_LOCAL_TTS_SHELL_OK');
} finally {
  await app.close();
}
