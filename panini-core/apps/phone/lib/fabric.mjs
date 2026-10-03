import { Socket } from './socket.mjs';
// The phone's fabric client (V2-14/V2-15): pairing, the session handshake, requests and signed decisions.
// Used by the page and by the service worker (notification actions), so it has no DOM dependencies.
import * as db from './db.mjs';
import { decision, decisionStatement, stale } from './cards.mjs';
import { b64, finish, hello, newDeviceKeys, sas } from './envelope.mjs';

/** Parse the pairing link's fragment: v, pc, ks, ke, c, exp and (relay mode) ch. */
export function parsePairing(hash) {
  const q = new URLSearchParams(String(hash || '').replace(/^#/, ''));
  if (q.get('v') !== '1' || !q.get('pc') || !q.get('ks') || !q.get('ke') || !q.get('c'))
    return null;
  return {
    pc: { id: q.get('pc'), signPub: q.get('ks'), agreePub: q.get('ke') },
    code: q.get('c'),
    expires: Number(q.get('exp') || 0),
    channel: q.get('ch') || '',
  };
}

/** The WebSocket address: the same origin (LAN, Tailscale) or the relay's device socket. */
export function socketUrl(origin, channel) {
  const url = new URL(origin);
  const scheme = url.protocol === 'https:' ? 'wss:' : 'ws:';
  return channel ? `${scheme}//${url.host}/d/${channel}` : `${scheme}//${url.host}/fabric`;
}

/** Pair this phone: create keys, send them with the code, and return the SAS to show. */
export async function pair(link, origin, name) {
  if (!link || !Number.isFinite(link.expires) || link.expires * 1000 <= Date.now())
    throw new Error('This pairing code has expired. Make a new code on your PC.');
  const keys = await newDeviceKeys();
  const url = socketUrl(origin, link.channel);
  const socket = new Socket(url);
  await socket.open();
  const reply = await socket.send({
    type: 'pair',
    message: { code: link.code, name, sign_pub: keys.signPub, agree_pub: keys.agreePub },
  });
  socket.close();
  if (reply.type !== 'paired') throw new Error(reply.error || 'Pairing failed.');
  const answer = reply.message;
  const shown = await sas(link.pc.signPub, keys.signPub, link.code);
  if (shown !== answer.sas) throw new Error('The codes do not match. Do not confirm on the PC.');
  await db.set('device', {
    id: answer.device_id,
    sign: keys.sign,
    agree: keys.agree,
    signPub: keys.signPub,
    name,
  });
  await db.set('pc', { ...link.pc, url });
  return shown;
}

/** One authenticated session to the PC. Re-handshakes silently when the socket drops. */
export class Fabric {
  static async load() {
    const device = await db.get('device');
    const pc = await db.get('pc');
    return device && pc ? new Fabric(device, pc) : null;
  }

  constructor(device, pc) {
    this.device = device;
    this.pc = pc;
    this.session = null;
    this.queue = Promise.resolve();
  }

  async connect() {
    this.socket?.close();
    this.socket = new Socket(this.pc.url);
    await this.socket.open();
    const opened = await hello(this.device, this.pc.id);
    const reply = await this.socket.send({ type: 'hello', message: opened.message });
    if (reply.type !== 'welcome') {
      if (/revoked|Unknown/.test(reply.error || ''))
        throw Object.assign(new Error(reply.error), { revoked: true });
      throw new Error(reply.error || 'Your PC refused the connection.');
    }
    this.session = await finish(this.device, opened, reply.message, this.pc);
  }

  request(method, path, body) {
    const next = this.queue.then(() => this.perform(method, path, body));
    this.queue = next.catch(() => {});
    return next;
  }

  async perform(method, path, body) {
    for (let attempt = 0; attempt < 2; attempt++) {
      try {
        if (!this.session) await this.connect();
        const sealed = await this.session.seal('req', { method, path, body: body ?? null });
        const reply = await this.socket.send({ type: 'env', envelope: sealed });
        if (reply.type !== 'env') throw new Error(reply.error || 'The request failed.');
        const { body: response } = await this.session.open(reply.envelope);
        return response;
      } catch (error) {
        this.session = null;
        this.socket?.close();
        if (error.revoked || method !== 'GET' || attempt === 1) throw error;
      }
    }
  }

  /** Decide an interrupt: restore it first, then sign exactly what was shown. */
  async decide(shown, button, answer) {
    const current = await this.request('GET', `/interrupts/${shown.id}`);
    const problem = stale(shown, current.status === 200 ? current.body : null);
    if (problem) return { ok: false, message: problem };
    const { status, payload } = decision(shown, button, answer);
    const statement = await decisionStatement(
      shown.id,
      shown.nonce,
      shown.action_digest,
      status,
      payload,
    );
    const signature = await globalThis.crypto.subtle.sign(
      { name: 'ECDSA', hash: 'SHA-256' },
      this.device.sign.privateKey,
      new TextEncoder().encode(statement),
    );
    const body = {
      nonce: shown.nonce,
      action_digest: shown.action_digest || null,
      status,
      payload,
      signature: b64(signature),
    };
    const reply = await this.request('POST', `/interrupts/${shown.id}/resolve`, body);
    return reply.status === 200
      ? { ok: true }
      : { ok: false, message: reply.body?.detail || 'The PC refused the decision.' };
  }

  async forget() {
    try {
      const reply = await this.request('DELETE', '/devices/self');
      if (reply.status !== 200)
        throw new Error(reply.body?.detail || 'The PC did not forget this phone.');
    } catch (error) {
      // Self-revocation can close the encrypted session before its acknowledgement arrives.
      // Confirm revocation with a new handshake; never replay the DELETE.
      try {
        await this.connect();
      } catch (confirmation) {
        if (confirmation.revoked) return;
      }
      throw error;
    } finally {
      await db.remove('device');
      await db.remove('pc');
    }
  }
}
