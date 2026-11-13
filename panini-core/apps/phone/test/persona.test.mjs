import test from 'node:test';
import assert from 'node:assert/strict';
import { addressedRequest, presentation } from '../lib/persona.mjs';

test('public persona presentation permits only known palette tokens', () => {
  assert.deepEqual(
    presentation({
      id: 'scout',
      name: '<script>Scout</script>',
      color: 'cyan',
      instructions: 'secret',
    }),
    { id: 'scout', name: '<script>Scout</script>', color: 'cyan' },
  );
  assert.equal(presentation({ id: 'scout', name: 'Scout', color: '__proto__' }).color, 'copper');
  assert.equal(presentation({ id: '../owner', name: 'Unsafe' }).id, 'panini');
  assert.equal(presentation(null).name, 'PANINI');
});
test('chooser explicitly addresses a persona without overriding a typed mention', () => {
  assert.equal(addressedRequest('Research this', 'scout'), '@scout Research this');
  assert.equal(addressedRequest('@Quill, write this', 'scout'), '@Quill, write this');
  assert.throws(() => addressedRequest('Hello', 'bad name'));
});
