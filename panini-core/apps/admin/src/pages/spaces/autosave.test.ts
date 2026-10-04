import { describe, it, expect, vi, afterEach } from 'vitest';
import { Autosave, type Draft } from './autosave';

afterEach(() => vi.useRealTimers());
describe('page autosave', () => {
  it('does not clear another editor draft after being unmounted during a save', async () => {
    let acknowledge!: (result: { revision: number }) => void;
    const persist = vi.fn();
    const queue = new Autosave(
      1,
      () =>
        new Promise((resolve) => {
          acknowledge = resolve;
        }),
      persist,
      vi.fn(),
    );
    queue.edit({ title: 'One', content: 'first', revision: 1 });
    const saving = queue.flush();
    queue.stop();
    persist.mockClear();
    acknowledge({ revision: 2 });
    await saving;
    expect(persist).not.toHaveBeenCalled();
  });
  it('retains edits made while a previous revision is saving', async () => {
    vi.useFakeTimers();
    let acknowledge!: (result: { revision: number }) => void;
    const save = vi
      .fn()
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            acknowledge = resolve;
          }),
      )
      .mockResolvedValue({ revision: 3 });
    let stored: Draft | null = null;
    const queue = new Autosave(
      1,
      save,
      (d) => {
        stored = d;
      },
      vi.fn(),
    );
    queue.edit({ title: 'One', content: 'first', revision: 1 });
    const saving = queue.flush();
    queue.edit({ title: 'One', content: 'second', revision: 1 });
    acknowledge({ revision: 2 });
    await saving;
    expect(stored).toEqual({ title: 'One', content: 'second', revision: 2 });
    await queue.flush();
    expect(save.mock.calls[1]![0].revision).toBe(2);
    expect(stored).toBeNull();
    queue.stop();
  });
  it('keeps offline and conflicting drafts and does not retry a conflict automatically', async () => {
    vi.useFakeTimers();
    const persist = vi.fn(),
      state = vi.fn();
    const save = vi.fn().mockRejectedValue({ status: 409 });
    const queue = new Autosave(1, save, persist, state);
    const draft = { title: 'Mine', content: 'Keep this', revision: 1 };
    queue.edit(draft);
    await queue.flush();
    await vi.advanceTimersByTimeAsync(10000);
    expect(save).toHaveBeenCalledTimes(1);
    expect(persist).toHaveBeenLastCalledWith(draft);
    expect(state).toHaveBeenLastCalledWith('conflict');
    queue.rebase(7);
    save.mockResolvedValue({ revision: 8 });
    await queue.flush();
    expect(save.mock.calls[1]![0].revision).toBe(7);
    queue.stop();
  });
});
