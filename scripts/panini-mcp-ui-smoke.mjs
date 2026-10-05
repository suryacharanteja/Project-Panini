// Built Admin with fixture API responses. SDK transport/approvals are tested separately.
import assert from 'node:assert/strict';
import { readFile, mkdir, writeFile } from 'node:fs/promises';
import { createServer } from 'node:http';
import { resolve, extname, sep } from 'node:path';
import { chromium } from 'playwright';

const root = resolve('frontend/public/admin');
const evidence = resolve('artifacts/mcp-server');
const server = createServer(async (req, res) => {
  const url = new URL(req.url, 'http://localhost');
  const asset = url.pathname.startsWith('/admin/') ? url.pathname.slice(6) : '/index.html';
  const path = resolve(root, '.' + (extname(asset) ? asset : '/index.html'));
  if (!path.startsWith(root + sep)) return res.writeHead(403).end();
  try {
    const mime = {
      '.html': 'text/html',
      '.js': 'application/javascript',
      '.css': 'text/css',
      '.svg': 'image/svg+xml',
    }[extname(path)];
    const bytes = await readFile(path);
    res.writeHead(200, { 'content-type': mime ?? 'application/octet-stream' }).end(bytes);
  } catch {
    res.writeHead(404).end();
  }
});
await new Promise((done) => server.listen(0, '127.0.0.1', done));
let browser;
try {
  await mkdir(evidence, { recursive: true });
  browser = await chromium.launch({ channel: 'chrome', headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const errors = [],
    requests = [];
  page.on('pageerror', (error) => errors.push(error.message));
  const identifier = 'a'.repeat(32);
  let clients = [],
    grants = [];
  await page.route('**/api/**', async (route) => {
    const request = route.request(),
      path = new URL(request.url()).pathname;
    const body = request.postDataJSON();
    if (body || request.method() === 'DELETE')
      requests.push({ path, method: request.method(), body });
    let data = {};
    if (path === '/api/admin/v1/agents') data = { agents: [], clients: [] };
    else if (path === '/api/admin/v1/mcp/clients' && request.method() === 'POST') {
      clients = [
        { id: identifier, name: body.name, grants: body.grants, revoked: null, last_used: null },
      ];
      data = {
        ...clients[0],
        token: 'mcp_fixture_token_for_ui_only',
        endpoint: 'http://127.0.0.1:3200/mcp',
        stdio: {
          command: 'C:/PANINI/cli/panini-mcp.exe',
          args: [
            '--client',
            identifier,
            '--token-file',
            `C:/PANINI/mcp/${identifier}/client.token`,
            '--url',
            'http://127.0.0.1:3200/mcp',
          ],
        },
        token_file: `C:\\PANINI\\mcp\\${identifier}\\client.token`,
      };
    } else if (path === '/api/admin/v1/mcp/clients')
      data = { clients, grants, enabled: true, ready: true };
    else if (path.endsWith('/grants') && request.method() === 'POST') {
      grants = [
        {
          id: 'temporary',
          subject: `mcp_client:${identifier}`,
          tools: [body.tool],
          expires_at: Date.now() / 1000 + body.seconds,
        },
      ];
      data = grants[0];
    } else if (path.endsWith(identifier) && request.method() === 'DELETE') {
      clients[0].revoked = Date.now() / 1000;
      data = { revoked: true };
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(data),
    });
  });
  await page.goto(`http://127.0.0.1:${server.address().port}/build/agents`);
  await page.getByText('Local MCP clients', { exact: true }).waitFor();
  await page.getByLabel('MCP client name').fill('Codex fixture');
  await page.getByRole('checkbox', { name: /Your attached Chrome/ }).check();
  await page.getByRole('button', { name: 'Register client' }).click();
  await page.getByLabel('One-time MCP setup').waitFor();
  assert(
    requests.some((r) => r.body?.grants?.includes('chrome') && r.body.grants.includes('browser')),
  );
  assert.match(await page.getByLabel('One-time MCP setup').innerText(), /claude mcp add/);
  assert.match(await page.getByLabel('One-time MCP setup').innerText(), /\[mcp_servers.panini\]/);
  await page.getByRole('button', { name: 'Allow for one hour' }).click();
  await page.getByText('browser_navigate · temporary permission', { exact: true }).waitFor();
  await page.screenshot({ path: resolve(evidence, 'admin-desktop.png'), fullPage: true });
  await page.setViewportSize({ width: 412, height: 915 });
  await page.waitForFunction(
    () => document.querySelector('aside').getBoundingClientRect().right <= 0,
  );
  assert(
    await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
    'Phone layout overflows',
  );
  await page.screenshot({ path: resolve(evidence, 'admin-mobile.png'), fullPage: true });
  await page.getByRole('button', { name: 'Revoke', exact: true }).click();
  await page.getByText('Revoked', { exact: true }).waitFor();
  assert.equal(await page.getByLabel('One-time MCP setup').count(), 0);
  assert.deepEqual(errors, []);
  const report = {
    passed: true,
    api: 'fixture responses; real browser-rendered built Admin',
    registrationScopes: true,
    httpAndStdioSetup: true,
    temporaryGrant: true,
    revokeHidesSecret: true,
    mobileNoOverflow: true,
    pageErrors: errors,
    requests,
  };
  await writeFile(resolve(evidence, 'ui-evidence.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report));
} finally {
  await browser?.close();
  server.closeAllConnections();
  await new Promise((done) => server.close(done));
}
