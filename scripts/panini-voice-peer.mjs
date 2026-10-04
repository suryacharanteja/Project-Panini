// Synthetic silent peer for the opt-in Python live probe; JSON lines over private pipes.
import { chromium } from 'playwright';
import { createInterface } from 'node:readline';
import { inspectPlayback } from './panini-voice-playback.mjs';
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
    window.playback = {
      frames: 0,
      voicedFrames: 0,
      firstVoiceAt: null,
      lastVoiceAt: null,
      peakRms: 0,
    };
    pc.ontrack = (event) => {
      const stream = new MediaStream([event.track]);
      const player = document.createElement('audio');
      player.srcObject = stream;
      player.volume = 0;
      player.autoplay = true;
      document.body.append(player);
      player.play().catch(() => {});
      window.remotePlayer = player;
      const source = audioContext.createMediaStreamSource(stream);
      const analyser = audioContext.createAnalyser();
      analyser.fftSize = 1024;
      const sink = audioContext.createGain();
      sink.gain.value = 0; // Render decoded audio without playing it through the user's speakers.
      source.connect(analyser).connect(sink).connect(audioContext.destination);
      window.remoteSource = source;
      const samples = new Float32Array(analyser.fftSize);
      window.playbackTimer = setInterval(() => {
        analyser.getFloatTimeDomainData(samples);
        const rms = Math.sqrt(
          samples.reduce((sum, value) => sum + value * value, 0) / samples.length,
        );
        const now = performance.now();
        playback.frames += 1;
        playback.peakRms = Math.max(playback.peakRms, rms);
        if (rms >= 0.003) {
          playback.voicedFrames += 1;
          playback.firstVoiceAt ??= now;
          playback.lastVoiceAt = now;
        }
      }, 20);
    };
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
    for await (const line of iterator) {
      const request = JSON.parse(line);
      if (request.end) break;
      if (request.playback) {
        const deadline = Date.now() + 30000;
        let metrics;
        do {
          metrics = await page.evaluate(async () => {
            const reports = await pc.getStats();
            const inbound = [...reports.values()].find(
              (report) => report.type === 'inbound-rtp' && report.kind === 'audio',
            );
            return {
              ...playback,
              now: performance.now(),
              contextState: audioContext.state,
              samplesReceived: inbound?.totalSamplesReceived ?? 0,
              jitterSamplesEmitted: inbound?.jitterBufferEmittedCount ?? 0,
              packetsLost: inbound?.packetsLost ?? 0,
              packetsReceived: inbound?.packetsReceived ?? 0,
              bytesReceived: inbound?.bytesReceived ?? 0,
              concealedSamples: inbound?.concealedSamples ?? 0,
              audioEnergy: inbound?.totalAudioEnergy ?? 0,
              trackMuted: remoteSource.mediaStream.getAudioTracks()[0].muted,
              playerPaused: remotePlayer.paused,
            };
          });
          if (inspectPlayback(metrics).passed) break;
          await new Promise((resolve) => setTimeout(resolve, 100));
        } while (Date.now() < deadline);
        process.stdout.write(
          JSON.stringify({ playback: metrics, ...inspectPlayback(metrics) }) + '\n',
        );
      }
    }
  }
} finally {
  commands.close();
  await browser.close();
}
