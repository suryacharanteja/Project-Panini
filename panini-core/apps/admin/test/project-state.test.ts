import { describe, expect, it } from 'vitest';
import { isVerified, taskCounts } from '../src/pages/project-state';

describe('project progress', () => {
  it('counts only completed outcomes with evidence as verified', () => {
    const tasks = [
      {
        id: 'a',
        title: 'A',
        status: 'completed',
        result: { outcome: 'verified_complete', evidence: ['source'] },
      },
      {
        id: 'b',
        title: 'B',
        status: 'completed',
        result: { outcome: 'verified_complete', evidence: [] },
      },
      { id: 'c', title: 'C', status: 'queued' },
      { id: 'd', title: 'D', status: 'needs_review' },
    ];
    expect(isVerified(tasks[0])).toBe(true);
    expect(isVerified(tasks[1])).toBe(false);
    expect(taskCounts(tasks)).toEqual({ verified: 1, working: 1, attention: 2 });
  });
});
