import { randomUUID } from 'node:crypto';

export class PaniniClient {
  token = '';
  sessionId = '';
  epoch = 0;
  cursor = 0;
  constructor(
    readonly base: string,
    readonly instance: string,
  ) {}
  async call(path: string, body?: object, method = body ? 'POST' : 'GET', timeoutMs = 45000) {
    const response = await fetch(`${this.base}/api/panini${path}`, {
      method,
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${this.token}`,
        'X-Panini-Instance': this.instance,
        'X-Panini-Lease-Epoch': String(this.epoch),
      },
      body: body ? JSON.stringify(body) : undefined,
      signal: AbortSignal.timeout(timeoutMs),
    });
    const bodyText = await response.text();
    let data;
    try {
      data = JSON.parse(bodyText);
    } catch {
      throw new Error(
        `PANINI service did not return a valid response (${response.status}). Restart or check provider configuration.`,
      );
    }
    if (!response.ok)
      throw new Error(data.error?.message || `PANINI request failed (${response.status}).`);
    return data;
  }
  scoped(path: string, body?: object, method?: string, timeoutMs?: number) {
    return this.call(`/sessions/${this.sessionId}${path}`, body, method, timeoutMs);
  }
  async connect(bootstrap: string) {
    this.token = bootstrap;
    const registered = await this.call('/clients', {
      client_name: 'PANINI Electron',
      profile_id: 'panini',
    });
    this.token = registered.token;
    const session =
      registered.sessions[0] || (await this.call('/sessions', { command_id: randomUUID() }));
    this.sessionId = session.session_id;
    const lease = await this.scoped('/lease/acquire', { command_id: randomUUID() });
    this.epoch = lease.lease_epoch;
    return this.scoped('');
  }
  async events(signal: AbortSignal, emit: (event: any) => Promise<void>) {
    while (!signal.aborted) {
      try {
        const response = await fetch(
          `${this.base}/api/panini/sessions/${this.sessionId}/events?after=${this.cursor}`,
          {
            headers: { Authorization: `Bearer ${this.token}`, 'X-Panini-Instance': this.instance },
            signal,
          },
        );
        if (response.status === 409) {
          const snapshot = await this.scoped('');
          this.cursor = snapshot.last_event_seq;
          await emit({ type: 'snapshot', data: snapshot });
          continue;
        }
        if (!response.ok || !response.body) throw new Error('Activity connection unavailable.');
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let pending = '';
        try {
          while (!signal.aborted) {
            const { value, done } = await reader.read();
            if (done) break;
            pending += decoder.decode(value, { stream: true });
            if (pending.length > 2 * 1024 * 1024)
              throw new Error('Activity frame exceeded its limit.');
            let index;
            while ((index = pending.indexOf('\n')) >= 0) {
              const line = pending.slice(0, index);
              pending = pending.slice(index + 1);
              if (!line) continue;
              const event = JSON.parse(line);
              if (event.sequence && event.sequence <= this.cursor) continue;
              if (event.sequence) this.cursor = event.sequence;
              await emit(event);
            }
          }
        } finally {
          await reader.cancel().catch(() => {});
        }
      } catch {
        if (!signal.aborted)
          await emit({
            type: 'connection.interrupted',
            data: { message: 'Reconnecting activity…' },
          });
      }
      if (!signal.aborted) await new Promise((resolve) => setTimeout(resolve, 1000));
    }
  }
}
