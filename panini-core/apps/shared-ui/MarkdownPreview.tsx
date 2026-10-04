import DOMPurify from 'dompurify';
import { Marked } from 'marked';

const markdown = new Marked();
const escape = (text: string) =>
  text.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;');
markdown.use({
  renderer: {
    image: ({ text }) => escape(text),
    html: ({ text }) => escape(text),
  },
});

/** Review rendering never executes document HTML or fetches images. */
export function MarkdownPreview({ content }: { content: string }) {
  if (typeof DOMPurify.sanitize !== 'function') return <pre className="pc-preview">{content}</pre>;
  const html = DOMPurify.sanitize(markdown.parse(content, { async: false }), {
    ALLOWED_TAGS: [
      'p',
      'h1',
      'h2',
      'h3',
      'h4',
      'h5',
      'h6',
      'strong',
      'em',
      'del',
      'ul',
      'ol',
      'li',
      'blockquote',
      'pre',
      'code',
      'table',
      'thead',
      'tbody',
      'tr',
      'th',
      'td',
      'hr',
      'br',
    ],
    ALLOWED_ATTR: [],
  });
  return (
    <div
      aria-label="Rendered draft"
      className="pc-preview whitespace-normal"
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}
