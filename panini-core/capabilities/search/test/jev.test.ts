import { afterEach, describe, expect, it, vi } from 'vitest';
import { judge, inferIntent } from '@/lib/jev';
const evaluate = vi.hoisted(() => vi.fn());
const gateway = vi.hoisted(() => vi.fn(() => ({ evaluationModel: () => 'test-jev-model' })));
vi.mock('ai', () => ({ experimental_evaluate: evaluate, createGateway: gateway }));
afterEach(() => vi.resetAllMocks());
describe('shared Jev gateway', () => {
  it('uses the supplied gateway key, validates probabilities and disables hidden retries', async () => {
    evaluate.mockResolvedValue({
      answers: { relevant: { type: 'boolean', probability: 0.8 } },
      usage: { inputTokens: 12, outputTokens: 0 },
    });
    expect(
      await judge(
        { apiKey: 'configured-key' },
        {},
        { relevant: { type: 'boolean', instructions: 'Relevant?' } },
      ),
    ).toMatchObject({ probabilities: { relevant: 0.8 }, usage: { input_tokens: 12 } });
    expect(gateway).toHaveBeenCalledWith({ apiKey: 'configured-key' });
    expect(evaluate.mock.calls[0]![0]).toMatchObject({ maxRetries: 0, model: 'test-jev-model' });
  });
  it.each([NaN, 1.5, -1, undefined])('rejects malformed probability %s', async (value) => {
    evaluate.mockResolvedValue({
      answers: { a: { type: 'boolean', probability: value } },
      usage: {},
    });
    await expect(
      judge({ apiKey: 'key' }, {}, { a: { type: 'boolean', instructions: 'A?' } }),
    ).rejects.toThrow('unavailable');
  });
  it('never exposes upstream request details', async () => {
    evaluate.mockRejectedValue(new Error('secret request body'));
    await expect(judge({ apiKey: 'key' }, {}, {})).rejects.toThrow('Jev evaluation is unavailable');
  });
  it('scores boolean choices instead of requiring the retired direct TypeSafe API', async () => {
    evaluate.mockImplementation(async ({ questions }) => ({
      answers: Object.fromEntries(
        Object.keys(questions).map((key) => [
          key,
          {
            type: 'boolean',
            probability:
              key === 'window_7d' || key === 'query_1' || key === 'source_github' ? 0.9 : 0.1,
          },
        ]),
      ),
      usage: { inputTokens: 10 },
    }));
    const intent = await inferIntent(
      { apiKey: 'key' },
      { request: 'repos this week', candidates: ['repos this week', 'repos'], now: new Date() },
    );
    expect(intent.window.choice).toBe('7d');
    expect(intent.query.index).toBe(1);
    expect(intent.sources.github).toBe(0.9);
  });
});
