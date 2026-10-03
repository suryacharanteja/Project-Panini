// Bounded request/reply socket. A lost reply is ambiguous: callers must not replay effects.
export class Socket {
  constructor(url, { WebSocketClass = globalThis.WebSocket, timeoutMs = 15000 } = {}) {
    this.url = url;
    this.WebSocketClass = WebSocketClass;
    this.timeoutMs = timeoutMs;
    this.waiting = [];
  }
  open() {
    return new Promise((resolve, reject) => {
      this.ws = new this.WebSocketClass(this.url);
      const timer = setTimeout(() => {
        reject(new Error('Connection timed out.'));
        this.close();
      }, this.timeoutMs);
      this.ws.onopen = () => {
        clearTimeout(timer);
        resolve();
      };
      this.ws.onerror = () => {
        clearTimeout(timer);
        reject(new Error('Your PC is offline or unreachable.'));
        this.close();
      };
      this.ws.onclose = () => {
        clearTimeout(timer);
        reject(new Error('The connection to your PC closed.'));
        this.fail(new Error('Connection closed. Check the PC before repeating an action.'));
      };
      this.ws.onmessage = (event) => {
        try {
          const frame = JSON.parse(event.data);
          const pending = this.waiting.shift();
          if (!pending) throw new Error('Unexpected response from the PC.');
          clearTimeout(pending.timer);
          pending.resolve(frame);
        } catch {
          this.fail(new Error('Invalid response from the PC.'));
          this.close();
        }
      };
    });
  }
  fail(error) {
    for (const pending of this.waiting.splice(0)) {
      clearTimeout(pending.timer);
      pending.reject(error);
    }
  }
  send(frame) {
    return new Promise((resolve, reject) => {
      if (this.ws?.readyState !== 1) return reject(new Error('The connection is not open.'));
      const pending = { resolve, reject };
      pending.timer = setTimeout(() => {
        this.fail(new Error('Response timed out. Check the PC before repeating an action.'));
        this.close();
      }, this.timeoutMs);
      this.waiting.push(pending);
      try {
        this.ws.send(JSON.stringify(frame));
      } catch (error) {
        this.fail(error);
        this.close();
      }
    });
  }
  close() {
    this.fail(new Error('Connection closed.'));
    this.ws?.close();
  }
}
