// PANINI remote fabric envelope (V2-14 §5.3), device side, in WebCrypto. Same protocol as backend/fabric/envelope.py:
// hello {device_id, eph, ts, sig(eph|ts|pc_id)}; the PC's welcome {eph, ts, sig(eph_p|eph_d|device_id)};
// key = HKDF-SHA256(ECDH(eph) | ECDH(agree), salt = pc_id|device_id, info = "panini-fabric-v1");
// messages {v, seq, kind, id, iv, ct} with AES-256-GCM and AAD "v|seq|kind|id".
const subtle = globalThis.crypto.subtle;
const enc = new TextEncoder();
const dec = new TextDecoder();
const SIGN = { name: 'ECDSA', namedCurve: 'P-256' };
const AGREE = { name: 'ECDH', namedCurve: 'P-256' };
const ECDSA_SHA256 = { name: 'ECDSA', hash: 'SHA-256' };

export const b64 = (bytes) =>
  btoa(String.fromCharCode(...new Uint8Array(bytes)))
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=+$/, '');

export const unb64 = (text) => {
  const padded = text.replace(/-/g, '+').replace(/_/g, '/') + '='.repeat((4 - (text.length % 4)) % 4);
  return Uint8Array.from(atob(padded), (c) => c.charCodeAt(0));
};

const concat = (...parts) => {
  const out = new Uint8Array(parts.reduce((n, p) => n + p.length, 0));
  let at = 0;
  for (const p of parts) {
    out.set(p, at);
    at += p.length;
  }
  return out;
};

/** New device keys; private halves are not extractable (the app keeps them in IndexedDB). */
export async function newDeviceKeys() {
  const sign = await subtle.generateKey(SIGN, false, ['sign', 'verify']);
  const agree = await subtle.generateKey(AGREE, false, ['deriveBits']);
  return {
    sign,
    agree,
    signPub: b64(await subtle.exportKey('raw', sign.publicKey)),
    agreePub: b64(await subtle.exportKey('raw', agree.publicKey)),
  };
}

/** The six digits both screens show at pairing. */
export async function sas(pcSignPub, deviceSignPub, code) {
  const data = concat(unb64(pcSignPub), unb64(deviceSignPub), enc.encode(code));
  const digest = new Uint8Array(await subtle.digest('SHA-256', data));
  let n = 0n;
  for (const byte of digest.slice(0, 8)) n = (n << 8n) | BigInt(byte);
  const six = String(n % 1000000n).padStart(6, '0');
  return `${six.slice(0, 3)} ${six.slice(3)}`;
}

const importPub = (text, algo, usages) => subtle.importKey('raw', unb64(text), algo, true, usages);

export async function hello(device, pcId, ts = Math.floor(Date.now() / 1000)) {
  const eph = await subtle.generateKey(AGREE, false, ['deriveBits']);
  const ephPub = b64(await subtle.exportKey('raw', eph.publicKey));
  const sig = await subtle.sign(ECDSA_SHA256, device.sign.privateKey, enc.encode(`${ephPub}|${ts}|${pcId}`));
  return { message: { device_id: device.id, eph: ephPub, ts, sig: b64(sig) }, eph, ephPub };
}

export async function finish(device, opened, welcome, pc) {
  const signedBy = await importPub(pc.signPub, SIGN, ['verify']);
  const statement = enc.encode(`${welcome.eph}|${opened.ephPub}|${device.id}`);
  if (!(await subtle.verify(ECDSA_SHA256, signedBy, unb64(welcome.sig), statement))) {
    throw new Error("The PC's welcome is not signed by your PC.");
  }
  const bits = async (priv, pub) =>
    new Uint8Array(
      await subtle.deriveBits({ name: 'ECDH', public: await importPub(pub, AGREE, []) }, priv, 256),
    );
  const material = concat(
    await bits(opened.eph.privateKey, welcome.eph),
    await bits(device.agree.privateKey, pc.agreePub),
  );
  const base = await subtle.importKey('raw', material, 'HKDF', false, ['deriveKey']);
  const key = await subtle.deriveKey(
    { name: 'HKDF', hash: 'SHA-256', salt: enc.encode(pc.id + device.id), info: enc.encode('panini-fabric-v1') },
    base,
    { name: 'AES-GCM', length: 256 },
    false,
    ['encrypt', 'decrypt'],
  );
  return new Session(key);
}

const DEVICE_PREFIX = Uint8Array.from([100, 101, 118, 0]); // "dev\0"
const PC_PREFIX = Uint8Array.from([112, 99, 0, 0]); // "pc\0\0"

function ivFor(prefix, seq) {
  const iv = new Uint8Array(12);
  iv.set(prefix);
  new DataView(iv.buffer).setBigUint64(4, BigInt(seq));
  return iv;
}

export class Session {
  constructor(key) {
    this.key = key;
    this.sendSeq = 0;
    this.recvSeq = -1;
  }

  async seal(kind, body, id = crypto.randomUUID().replace(/-/g, '').slice(0, 16)) {
    const seq = ++this.sendSeq;
    const iv = ivFor(DEVICE_PREFIX, seq);
    const ct = await subtle.encrypt(
      { name: 'AES-GCM', iv, additionalData: enc.encode(`1|${seq}|${kind}|${id}`) },
      this.key,
      enc.encode(JSON.stringify(body)),
    );
    return { v: 1, seq, kind, id, iv: b64(iv), ct: b64(ct) };
  }

  async open(message) {
    if (message.v !== 1 || message.seq <= this.recvSeq) throw new Error('Replayed or unsupported message.');
    const iv = unb64(message.iv);
    if (b64(iv) !== b64(ivFor(PC_PREFIX, message.seq))) throw new Error('Unexpected IV.');
    const plain = await subtle.decrypt(
      { name: 'AES-GCM', iv, additionalData: enc.encode(`1|${message.seq}|${message.kind}|${message.id}`) },
      this.key,
      unb64(message.ct),
    );
    this.recvSeq = message.seq;
    return { kind: message.kind, id: message.id, body: JSON.parse(dec.decode(plain)) };
  }
}
