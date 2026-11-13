// Public presentation only; palette values never come from arbitrary CSS supplied by a server.
export const PALETTE = {
  copper: [184, 115, 51],
  cyan: [78, 205, 214],
  violet: [185, 156, 255],
  green: [118, 210, 160],
  amber: [255, 191, 0],
  rose: [242, 146, 177],
};
export function presentation(value) {
  if (!value || !/^[a-z][a-z0-9-]{0,40}$/.test(value.id || '') || typeof value.name !== 'string')
    return { id: 'panini', name: 'PANINI', color: 'copper' };
  return {
    id: value.id,
    name: value.name.slice(0, 80) || 'PANINI',
    color: Object.hasOwn(PALETTE, value.color) ? value.color : 'copper',
  };
}
export function addressedRequest(text, id) {
  if (!/^[a-z][a-z0-9-]{0,40}$/.test(id)) throw new Error('Choose a configured specialist.');
  return /^@[a-z][a-z0-9-]{0,40}(?=\s|[,.:!?]|$)/i.test(text) ? text : `@${id} ${text}`;
}
