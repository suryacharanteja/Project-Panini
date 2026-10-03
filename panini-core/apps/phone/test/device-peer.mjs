// A real WebCrypto device for backend/tests/fabric/test_interop.py: JSON lines on stdin and stdout.
import { createInterface } from 'node:readline';
import { finish, hello, newDeviceKeys, sas } from '../lib/envelope.mjs';

const say = (value) => process.stdout.write(JSON.stringify(value) + '\n');
const keys = await newDeviceKeys();
let pc;
let device;
let opened;
let session;
say({ step: 'keys', signPub: keys.signPub, agreePub: keys.agreePub });

for await (const line of createInterface({ input: process.stdin })) {
  const msg = JSON.parse(line);
  if (msg.step === 'paired') {
    pc = msg.pc;
    device = { ...keys, id: msg.device_id };
    say({ step: 'sas', sas: await sas(pc.signPub, keys.signPub, msg.code) });
  } else if (msg.step === 'hello') {
    opened = await hello(device, pc.id);
    say({ step: 'hello', message: opened.message });
  } else if (msg.step === 'welcome') {
    session = await finish(device, opened, msg.message, pc);
    say({ step: 'request', envelope: await session.seal('req', { method: 'GET', path: '/status' }) });
  } else if (msg.step === 'response') {
    const reply = await session.open(msg.envelope);
    let replay = 'accepted';
    try {
      await session.open(msg.envelope);
    } catch {
      replay = 'rejected';
    }
    say({ step: 'done', reply, replay });
    process.exit(0);
  }
}
