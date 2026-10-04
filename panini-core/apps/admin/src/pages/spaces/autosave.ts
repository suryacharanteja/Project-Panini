export type Draft = { title: string; content: string; revision: number };
export type SaveState = 'saved' | 'unsaved' | 'saving' | 'failed' | 'conflict';

/** One in-flight save, with newer edits retained until their own acknowledgement. */
export class Autosave {
  private pending: Draft | null = null;
  private active = false;
  private stopped = false;
  private timer: ReturnType<typeof setTimeout> | undefined;
  private revision: number;
  constructor(
    revision: number,
    private save: (draft: Draft) => Promise<{ revision: number }>,
    private persist: (draft: Draft | null) => void,
    private status: (state: SaveState) => void,
    private delay = 1500,
  ) {
    this.revision = revision;
  }

  edit(draft: Draft) {
    this.pending = { ...draft, revision: this.revision };
    this.persist(this.pending);
    this.status('unsaved');
    clearTimeout(this.timer);
    this.timer = setTimeout(() => void this.flush(), this.delay);
  }

  async flush() {
    if (this.active || this.stopped || !this.pending) return;
    clearTimeout(this.timer);
    const sent = this.pending;
    this.active = true;
    this.status('saving');
    try {
      const saved = await this.save({ ...sent, revision: this.revision });
      if (this.stopped) return; // An unmounted editor must not erase a newer editor's local draft.
      this.revision = saved.revision;
      if (this.pending === sent) {
        this.pending = null;
        this.persist(null);
        this.status('saved');
      } else if (this.pending) {
        this.pending = { ...this.pending, revision: this.revision };
        this.persist(this.pending);
        this.status('unsaved');
      }
    } catch (error) {
      this.status((error as { status?: number }).status === 409 ? 'conflict' : 'failed');
      return;
    } finally {
      this.active = false;
    }
    if (this.pending && !this.stopped) this.timer = setTimeout(() => void this.flush(), this.delay);
  }

  rebase(revision: number) {
    this.revision = revision;
  }
  stop() {
    this.stopped = true;
    clearTimeout(this.timer);
  }
}
