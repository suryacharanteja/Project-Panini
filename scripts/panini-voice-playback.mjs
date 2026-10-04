// A bounded probe for decoded speech plus a quiet tail, not human listening or turn completion.
export function inspectPlayback(metrics) {
  const quietMs = metrics.lastVoiceAt === null ? 0 : metrics.now - metrics.lastVoiceAt;
  const voicedMs = metrics.voicedFrames * 20;
  const passed =
    metrics.contextState === 'running' &&
    metrics.samplesReceived >= 24000 &&
    metrics.jitterSamplesEmitted >= 24000 &&
    metrics.peakRms >= 0.003 &&
    voicedMs >= 400 &&
    quietMs >= 2500;
  return { passed, voicedMs, quietMs };
}
