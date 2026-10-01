import { memoryCache } from '@/lib/cache';

const cache = memoryCache();

/** Secrets are read only in server handlers, inherited from the root launcher. */
export function getEnv() {
  const OPENAI_API_KEY = process.env.OPENAI_API_KEY;
  const AI_GATEWAY_API_KEY = process.env.AI_GATEWAY_API_KEY;
  if (!OPENAI_API_KEY || OPENAI_API_KEY.startsWith('your_')) {
    throw new Error('OPENAI_API_KEY is not configured');
  }
  if (!AI_GATEWAY_API_KEY || AI_GATEWAY_API_KEY.startsWith('your_')) {
    throw new Error('AI_GATEWAY_API_KEY is not configured');
  }
  return {
    OPENAI_API_KEY,
    AI_GATEWAY_API_KEY,
    CACHE: cache,
  };
}
