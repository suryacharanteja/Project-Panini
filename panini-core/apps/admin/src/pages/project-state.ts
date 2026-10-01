export type ProjectTask = {
  id: string;
  title: string;
  status: string;
  result?: { outcome?: string; message?: string; evidence?: unknown[] };
};

export function isVerified(task?: ProjectTask): boolean {
  return (
    task?.status === 'completed' &&
    task.result?.outcome === 'verified_complete' &&
    !!task.result.evidence?.length
  );
}

export function taskCounts(tasks: ProjectTask[]) {
  return tasks.reduce(
    (counts, task) => {
      if (isVerified(task)) counts.verified++;
      else if (task.status === 'running' || task.status === 'queued') counts.working++;
      else if (
        ['failed', 'needs_review', 'cancelled'].includes(task.status) ||
        task.status === 'completed'
      )
        counts.attention++;
      return counts;
    },
    { verified: 0, working: 0, attention: 0 },
  );
}
