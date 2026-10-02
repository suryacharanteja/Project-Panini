// Pinned upstreams PANINI v2 builds on (docs/panini-enhancements/lld/V2-00-program-setup.md).
// Fail closed when a pinned contract changes, so upgrades are deliberate and reviewed.
import { existsSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const failures = [];

const PLAYWRIGHT = '1.63.0';
const pkg = join(root, 'node_modules/playwright-core/package.json');
if (!existsSync(pkg)) failures.push('playwright-core is not installed (run npm ci).');
else {
  const version = JSON.parse(readFileSync(pkg, 'utf8')).version;
  if (version !== PLAYWRIGHT)
    failures.push(`playwright-core is ${version}; PANINI pins ${PLAYWRIGHT}.`);
  if (!existsSync(join(root, 'node_modules/playwright-core/lib/entry/mcp.js')))
    failures.push('playwright-core no longer ships lib/entry/mcp.js (the Playwright MCP server).');
}

const catalogue = join(root, 'panini-core/capabilities/browser/playwright/tools-1.63.0.json');
if (existsSync(catalogue)) {
  const names = JSON.parse(readFileSync(catalogue, 'utf8')).tools.map((t) => t.name);
  if (names.length !== new Set(names).size)
    failures.push('Duplicate names in the pinned Playwright tool catalogue.');
}

const compose = join(root, 'references/opendots/compose.computers.yml');
const OPENBOT = 'b6932d31a8d6e7896c15139dfc27a6c6911deb27';
if (existsSync(compose) && !readFileSync(compose, 'utf8').includes(OPENBOT))
  failures.push(`The OpenDots reference no longer pins OpenBot ${OPENBOT}.`);

if (failures.length) {
  for (const failure of failures) console.error('pin check:', failure);
  process.exitCode = 1;
} else console.log('pin check: ok');
