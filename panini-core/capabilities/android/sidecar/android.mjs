// Fixed Android transport. No arbitrary shell operation or selector is exposed to the model.
import { createRequire } from 'node:module';
import readline from 'node:readline';

const require = createRequire(new URL('../../browser/playwright/package.json', import.meta.url));
const { _android } = require('playwright-core');
const lines = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
const quote = (text) => "'" + text.replaceAll("'", "'\\''") + "'";
const keys = {
  BACK: 'Back',
  HOME: 'Home',
  ENTER: 'Enter',
  TAB: 'Tab',
  DEL: 'Del',
  ESCAPE: 'Escape',
  DPAD_UP: 'DpadUp',
  DPAD_DOWN: 'DpadDown',
};
const selectors = new Set(['clazz', 'res', 'desc', 'text', 'pkg']);
let devices;

async function call(request) {
  if (request.consent !== true) throw new Error('Driver installation consent is required.');
  devices ??= await _android.devices({ omitDriverInstall: false });
  if (request.op === 'devices')
    return devices.map((d) => ({ serial: d.serial(), model: d.model() }));
  const device = devices.find((d) => d.serial() === request.serial);
  if (!device) throw new Error('The selected phone is disconnected or unauthorized.');
  device.setDefaultTimeout(10000);
  const selector = request.selector;
  if (
    selector &&
    Object.entries(selector).some(([k, v]) => !selectors.has(k) || typeof v !== 'string')
  )
    throw new Error('Invalid fixed selector.');
  switch (request.op) {
    case 'snapshot':
      return (await device.shell('uiautomator dump /dev/tty')).toString('utf8');
    case 'screenshot':
      return (await device.screenshot()).toString('base64');
    case 'tap':
      await device.tap(selector);
      break;
    case 'longtap':
      await device.longTap(selector);
      break;
    case 'fill':
      if (typeof request.text !== 'string' || request.text.length > 10000)
        throw new Error('Invalid text.');
      await device.fill(selector, request.text);
      break;
    case 'scroll':
    case 'swipe':
      if (!['up', 'down', 'left', 'right'].includes(request.direction))
        throw new Error('Invalid direction.');
      await device.swipe(selector, request.direction, 0.6);
      break;
    case 'press':
      if (!Object.hasOwn(keys, request.key)) throw new Error('Invalid key.');
      await device.input.press(keys[request.key]);
      break;
    case 'launch':
      if (!/^[a-zA-Z][\w]*(\.[a-zA-Z][\w]*)+$/.test(request.package))
        throw new Error('Invalid package.');
      await device.shell(`monkey -p ${request.package} -c android.intent.category.LAUNCHER 1`);
      break;
    case 'url': {
      const url = new URL(request.url);
      if (
        !['http:', 'https:'].includes(url.protocol) ||
        url.username ||
        url.password ||
        request.url.length > 2048
      )
        throw new Error('Only http(s) URLs without credentials are allowed.');
      await device.shell(`am start -a android.intent.action.VIEW -d ${quote(url.href)}`);
      break;
    }
    default:
      throw new Error('Unknown Android operation.');
  }
  return { dispatched: true };
}

try {
  for await (const line of lines) {
    if (line.length > 50000) throw new Error('Request too large.');
    try {
      const result = await call(JSON.parse(line));
      process.stdout.write(JSON.stringify({ ok: true, result }) + '\n');
    } catch {
      // Raw driver errors may include typed values. Keep them inside this process.
      process.stdout.write(
        JSON.stringify({
          ok: false,
          error: 'Android operation failed; check the phone and debugging authorization.',
        }) + '\n',
      );
    }
  }
} finally {
  for (const device of devices || []) await device.close().catch(() => {});
}
