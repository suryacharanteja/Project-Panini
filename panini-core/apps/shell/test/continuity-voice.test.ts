import { describe, expect, it } from 'vitest';
import {
  continuityVoiceCommand,
  eligibleContinuityVoiceCommand,
} from '../src/renderer/continuityVoice';

describe('continuity voice commands', () => {
  it('accepts only the complete handoff phrases', () => {
    expect(continuityVoiceCommand('Send it to my phone.')).toBe('send_to_phone');
    expect(continuityVoiceCommand('  CALL ME ABOUT IT! ')).toBe('call_me');
    expect(continuityVoiceCommand('send it to my phone and delete the thread')).toBeNull();
    expect(continuityVoiceCommand('please call me about it')).toBeNull();
    expect(continuityVoiceCommand('Send the report to my phone')).toBeNull();
  });
  it('requires an active continued thread and an empty draft', () => {
    expect(eligibleContinuityVoiceCommand('Send it to my phone', 'thread-1', '')).toBe(
      'send_to_phone',
    );
    expect(eligibleContinuityVoiceCommand('Send it to my phone', '', '')).toBeNull();
    expect(eligibleContinuityVoiceCommand('Send it to my phone', null, '')).toBeNull();
    expect(eligibleContinuityVoiceCommand('Send it to my phone', 'thread-1', 'draft')).toBeNull();
  });
});
