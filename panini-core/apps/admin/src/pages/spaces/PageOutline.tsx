import type { Editor } from '@tiptap/react';

export function PageOutline({
  editor,
  onJump,
}: {
  editor: Editor | null;
  onJump: (position: number) => void;
}) {
  const headings: { text: string; position: number; level: number }[] = [];
  editor?.state.doc.descendants((node, position) => {
    if (node.type.name === 'heading')
      headings.push({ text: node.textContent, position, level: node.attrs.level });
  });
  return (
    <nav aria-label="Document outline" className="grid gap-2 mb-4">
      {headings.map((heading) => (
        <button
          key={heading.position}
          className="text-left text-sm text-muted hover:text-copper"
          style={{ paddingLeft: (heading.level - 1) * 12 }}
          onClick={() => onJump(heading.position)}
        >
          {heading.text || 'Untitled heading'}
        </button>
      ))}
      {!headings.length && (
        <p className="text-sm text-muted">Add headings to navigate your document.</p>
      )}
    </nav>
  );
}
