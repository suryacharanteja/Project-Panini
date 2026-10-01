import type { AvatarState } from './Avatar';

/** What the avatar shows, from what PANINI is actually doing (Visibility of System Status).
 *
 * Priority: a person-needed moment (approval) beats work in progress, which beats the
 * microphone; the success flash yields to listening as soon as the microphone is live.
 */
export function avatarStateFor({
  phase,
  ready,
  mic,
  muted,
}: {
  phase: string;
  ready: boolean;
  mic: boolean;
  muted: boolean;
}): AvatarState {
  if (phase === 'connecting' || phase === 'suspended') return 'offline';
  if (phase === 'awaiting_confirmation' || phase === 'needs_input') return 'approval';
  if (phase === 'preparing') return 'thinking';
  const settled = ['completed', 'blocked', 'failed', 'stopped', 'ready'].includes(phase);
  if (!settled) return 'working';
  if (!ready && phase !== 'completed') return 'offline';
  if (phase === 'blocked' || phase === 'failed') return 'blocked';
  if (mic) return muted ? 'muted' : 'listening';
  if (phase === 'completed') return 'success';
  return 'idle';
}
