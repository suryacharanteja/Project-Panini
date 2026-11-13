export type ContinuityVoiceCommand = 'send_to_phone' | 'call_me';

/** Only complete, unambiguous utterances may trigger a continuity action. */
export function continuityVoiceCommand(transcript: string): ContinuityVoiceCommand | null {
  const spoken = transcript
    .trim()
    .toLowerCase()
    .replace(/[.!?]+$/g, '')
    .replace(/\s+/g, ' ');
  if (spoken === 'send it to my phone' || spoken === 'send this to my phone')
    return 'send_to_phone';
  if (spoken === 'call me about it' || spoken === 'review a call to me') return 'call_me';
  return null;
}

export function eligibleContinuityVoiceCommand(
  transcript: string,
  threadId: unknown,
  existingDraft: string,
): ContinuityVoiceCommand | null {
  if (typeof threadId !== 'string' || !threadId.trim() || existingDraft.trim()) return null;
  return continuityVoiceCommand(transcript);
}
