import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import { test } from 'node:test';
import assert from 'node:assert/strict';

function fixture() {
  const elements = new Map();
  const element = (id) => {
    if (!elements.has(id))
      elements.set(id, { innerHTML: '', textContent: '', classList: { remove() {}, add() {} } });
    return elements.get(id);
  };
  const context = vm.createContext({
    document: { getElementById: element, addEventListener() {} },
  });
  vm.runInContext(readFileSync(new URL('../public/app.js', import.meta.url), 'utf8'), context);
  return { context, element, run: (code) => vm.runInContext(code, context) };
}

test('provider text and malformed usage cannot insert markup into comparison cards', () => {
  const f = fixture();
  f.context.result = {
    result: '<img src=x onerror=alert(1)>',
    reasoning: '<script>bad()</script>',
    tokens: '<img src=x>',
    latency_ms: 1,
    answers: {},
  };
  const html = f.run('card(result)');
  assert.doesNotMatch(html, /<img|<script/);
  assert.match(html, /&lt;img/);
  assert.match(html, /Tokens/);
});

test('bulk metrics exclude failed pairs instead of counting them as free or instant', () => {
  const f = fixture();
  f.context.results = [
    { direct: { latency_ms: 100, cost: 1 }, jev: { latency_ms: 50, cost: 0.5 } },
    { direct: { error: 'unavailable', latency_ms: 0, cost: 0 }, jev: { latency_ms: 0, cost: 0 } },
  ];
  f.run('displayMetrics(results)');
  const html = f.element('comparison-strip').innerHTML;
  assert.match(html, /1\/2 successful pairs/);
  assert.match(html, /Direct 100ms, Jev 50ms/);
  assert.match(html, /50\.0%/);
});
