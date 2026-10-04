// Synthetic silent peer for the opt-in Python live probe; JSON lines over private pipes.
import { chromium } from 'playwright';
import { createInterface } from 'node:readline';
const commands = createInterface({ input: process.stdin, terminal: false });
const iterator = commands[Symbol.asyncIterator]();
const browser = await chromium.launch({
  channel: 'chrome',
  headless: true,
  args: ['--autoplay-policy=no-user-gesture-required'],
});
try {
  const page = await browser.newPage();
  const sdp = await page.evaluate(async () => {
    window.pc = new RTCPeerConnection();
    window.audioContext = new AudioContext();
    const target = audioContext.createMediaStreamDestination();
    const silence = audioContext.createConstantSource();
    silence.offset.value = 0;
    silence.connect(target);
    silence.start();
    await audioContext.resume();
    target.stream.getTracks().forEach((t) => pc.addTrack(t, target.stream));
    pc.createDataChannel('oai-events');
    await pc.setLocalDescription(await pc.createOffer());
    await new Promise((resolve) => {
      const timer = setTimeout(resolve, 2500);
      pc.onicegatheringstatechange = () => {
        if (pc.iceGatheringState === 'complete') {
          clearTimeout(timer);
          resolve();
        }
      };
    });
    return pc.localDescription.sdp;
  });
  process.stdout.write(JSON.stringify({ sdp }) + '\n');
  const next = await iterator.next();
  const command = next.done ? { end: true } : JSON.parse(next.value);
  if (!command.end) {
    await page.evaluate(
      async (sdp) => pc.setRemoteDescription({ type: 'answer', sdp }),
      command.sdp,
    );
    await page.waitForFunction(() => pc.connectionState === 'connected', null, { timeout: 20000 });
    process.stdout.write(JSON.stringify({ connected: true }) + '\n');
    await iterator.next();
  }
} finally {
  commands.close();
  await browser.close();
}
