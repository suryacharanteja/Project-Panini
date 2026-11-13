import { test } from 'node:test';
import assert from 'node:assert/strict';
import { inspectPlayback } from '../../scripts/panini-voice-playback.mjs';

const speech = {
  contextState: 'running',
  samplesReceived: 48000,
  jitterSamplesEmitted: 48000,
  peakRms: 0.12,
  voicedFrames: 70,
  lastVoiceAt: 2000,
  now: 5000,
};

test('playback probe requires decoded speech and a sustained quiet tail', () => {
  assert.equal(inspectPlayback(speech).passed, true);
  for (const delta of [
    { contextState: 'suspended' },
    { samplesReceived: 0 },
    { jitterSamplesEmitted: 0 },
    { peakRms: 0 },
    { voicedFrames: 0 },
    { lastVoiceAt: null },
    { now: 2100 },
  ])
    assert.equal(inspectPlayback({ ...speech, ...delta }).passed, false);
});
