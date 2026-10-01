import { describe, expect, it } from 'vitest';
import { avatarStateFor } from '../src/renderer/avatar/state';

const at = (phase: string, extra: Partial<{ ready: boolean; mic: boolean; muted: boolean }> = {}) =>
  avatarStateFor({ phase, ready: true, mic: false, muted: false, ...extra });

describe('the avatar mirrors what PANINI is doing', () => {
  it('connects, then rests', () => {
    expect(at('connecting', { ready: false })).toBe('offline');
    expect(at('ready')).toBe('idle');
  });
  it('thinks, delegates and works', () => {
    expect(at('preparing')).toBe('thinking');
    for (const phase of ['running', 'verifying', 'dispatching']) expect(at(phase)).toBe('working');
  });
  it('holds for the person when approval or input is needed, even with the mic on', () => {
    expect(at('awaiting_confirmation', { mic: true })).toBe('approval');
    expect(at('needs_input')).toBe('approval');
  });
  it('flashes success, then listens when the microphone is live', () => {
    expect(at('completed')).toBe('success');
    expect(at('completed', { mic: true })).toBe('listening');
    expect(at('ready', { mic: true, muted: true })).toBe('muted');
  });
  it('shows a calm blocked state for blocked and failed work', () => {
    expect(at('blocked')).toBe('blocked');
    expect(at('failed', { mic: true })).toBe('blocked');
  });
  it('work in progress beats the microphone', () => {
    expect(at('running', { mic: true })).toBe('working');
  });
});
