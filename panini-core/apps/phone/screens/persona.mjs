import { presentation } from '../lib/persona.mjs';
import { h } from './ui.mjs';
export function personaChip(value) {
  const p = presentation(value);
  return h('span.persona-chip', { 'data-color': p.color, text: p.name });
}
