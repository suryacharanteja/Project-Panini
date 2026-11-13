import { chromium } from 'playwright-core';
import { marked } from 'marked';

// A fresh, credential-free context. User documents never execute script or load resources.
let input = '';
process.stdin.setEncoding('utf8');
for await (const chunk of process.stdin) {
  input += chunk;
  if (input.length > 2500000) throw new Error('Document exceeds export limit');
}
const pageData = JSON.parse(input);
const escape = (value) =>
  String(value)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;');
marked.use({
  renderer: {
    html: ({ text }) => escape(text),
    image: ({ text }) => escape(text || '[image]'),
    link: function ({ tokens }) {
      return this.parser.parseInline(tokens);
    },
  },
});
let browser;
try {
  try {
    browser = await chromium.launch({ channel: 'msedge', headless: true });
  } catch {
    browser = await chromium.launch({ channel: 'chrome', headless: true });
  }
  const context = await browser.newContext({ javaScriptEnabled: false, serviceWorkers: 'block' });
  await context.route('**/*', (route) => route.abort());
  const page = await context.newPage();
  await page.setContent(`<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'"><style>
  body{font:12px/1.6 Arial,sans-serif;color:#20242a}h1{font-size:26px}h2{font-size:20px}h3{font-size:16px}h1,h2,h3{break-after:avoid}pre{white-space:pre-wrap;background:#f0f2f4;padding:12px}code{overflow-wrap:anywhere}table{border-collapse:collapse;width:100%}td,th{border:1px solid #bbb;padding:7px}blockquote{border-left:3px solid #bbb;padding-left:12px}p,li{overflow-wrap:anywhere}img{display:none}
  </style></head><body><h1>${escape(pageData.title)}</h1><p>Revision ${Number(pageData.revision)}</p>${marked.parse(pageData.content)}</body></html>`);
  const bytes = await page.pdf({
    format: 'A4',
    printBackground: true,
    margin: { top: '18mm', bottom: '18mm', left: '18mm', right: '18mm' },
  });
  process.stdout.write(bytes);
} finally {
  await browser?.close();
}
