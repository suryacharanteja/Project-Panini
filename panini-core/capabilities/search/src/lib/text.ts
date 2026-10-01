const ENTITIES: Record<string, string> = {
  amp: '&',
  lt: '<',
  gt: '>',
  quot: '"',
  apos: "'",
  nbsp: ' ',
  middot: '·',
  hellip: '…',
  mdash: '—',
  ndash: '–',
  lsquo: '‘',
  rsquo: '’',
  ldquo: '“',
  rdquo: '”',
  laquo: '«',
  raquo: '»',
};

/** Snippets come back with HTML entities left in; decode the common ones. */
export function decodeEntities(text: string): string {
  const decode = (match: string, value: number) =>
    Number.isInteger(value) && value >= 0 && value <= 0x10ffff
      ? String.fromCodePoint(value)
      : match;
  return text
    .replace(/&#x([0-9a-f]+);/gi, (match, hex: string) => decode(match, Number.parseInt(hex, 16)))
    .replace(/&#(\d+);/g, (match, dec: string) => decode(match, Number(dec)))
    .replace(/&([a-z]+);/gi, (match, name: string) => ENTITIES[name.toLowerCase()] ?? match)
    .replace(/\s{2,}/g, ' ');
}
