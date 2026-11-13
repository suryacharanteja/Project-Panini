import { EditorContent, useEditor } from '@tiptap/react';
import StarterKit from '@tiptap/starter-kit';
import { Markdown } from '@tiptap/markdown';
import { TableKit } from '@tiptap/extension-table';
import TaskList from '@tiptap/extension-task-list';
import TaskItem from '@tiptap/extension-task-item';
import { useEffect, useRef, useState } from 'react';
import { api, harness } from '../../api';
import { Button, Card, inputClass } from '../../components/ui';
import { Autosave, type Draft, type SaveState } from './autosave';
import { ConversationsPage } from '../Conversations';
import './editor.css';

import type { Document, PageRevision } from './types';
import { PageOutline } from './PageOutline';
const base = harness('/spaces');

export function PageDocument({ page }: { page: Document }) {
  const key = `panini.page.draft.${page.id}`;
  const recovered = (() => {
    try {
      const value = JSON.parse(localStorage.getItem(key) ?? 'null');
      return value &&
        typeof value.title === 'string' &&
        typeof value.content === 'string' &&
        Number.isInteger(value.revision)
        ? (value as Draft)
        : null;
    } catch {
      return null;
    }
  })();
  const [title, setTitle] = useState(recovered?.title ?? page.title);
  const [content, setContent] = useState(recovered?.content ?? page.content);
  const [source, setSource] = useState(false);
  const [thread, setThread] = useState('');
  const [slash, setSlash] = useState(false);
  const [state, setState] = useState<SaveState>(
    recovered ? (recovered.revision === page.revision ? 'unsaved' : 'conflict') : 'saved',
  );
  const [message, setMessage] = useState(recovered ? 'Recovered your local draft.' : '');
  const [history, setHistory] = useState<PageRevision[]>([]);
  const [outline, setOutline] = useState(false);
  const [conversationPrompt, setConversationPrompt] = useState('');
  async function discuss(prompt = '') {
    try {
      const result = await api<{ thread_id: string }>(`${base}/pages/${page.id}/conversation`, {
        method: 'POST',
      });
      setConversationPrompt(prompt);
      setThread(result.thread_id);
    } catch (error) {
      setMessage((error as Error).message);
    }
  }
  const latest = useRef({ title, content });
  latest.current = { title, content };
  const queue = useRef<Autosave | null>(null);
  const editor = useEditor({
    extensions: [
      StarterKit.configure({
        link: { openOnClick: false, protocols: ['https', 'http', 'mailto'] },
      }),
      Markdown,
      TableKit,
      TaskList,
      TaskItem.configure({ nested: true }),
    ],
    content,
    contentType: 'markdown',
    editorProps: {
      attributes: { class: 'panini-document min-h-80 p-5 outline-none' },
      handleKeyDown: (_view, event) => {
        if (event.key === '/') {
          setSlash(true);
          event.preventDefault();
          return true;
        }
        if (event.key === 'Escape') setSlash(false);
        return false;
      },
    },
    onUpdate: ({ editor }) => {
      const next = editor.getMarkdown();
      setContent(next);
      queue.current?.edit({ ...latest.current, content: next, revision: page.revision });
    },
  });
  useEffect(() => {
    const autosave = new Autosave(
      recovered?.revision ?? page.revision,
      (draft) =>
        api<Document>(`${base}/pages/${page.id}`, {
          method: 'PATCH',
          body: { expected_revision: draft.revision, title: draft.title, content: draft.content },
        }),
      (draft) => {
        try {
          draft ? localStorage.setItem(key, JSON.stringify(draft)) : localStorage.removeItem(key);
        } catch {
          setMessage(
            'Local draft storage is unavailable. Keep this page open and copy your draft before leaving.',
          );
        }
      },
      setState,
    );
    queue.current = autosave;
    if (recovered && recovered.revision === page.revision) autosave.edit(recovered);
    return () => {
      autosave.stop();
      queue.current = null;
    };
  }, [page.id]);

  async function rebase() {
    try {
      const current = await api<Document>(`${base}/pages/${page.id}`);
      setMessage(
        `Server revision ${current.revision}:\n${current.content}\n\nYour draft remains in the editor. Merge it here, then press Save again.`,
      );
      // Explicit confirmation is needed before a conflicting draft can overwrite the new revision.
      if (
        window.confirm(
          `Replace revision ${current.revision} with your current draft? Cancel to merge first.`,
        )
      ) {
        queue.current?.rebase(current.revision);
        queue.current?.edit({ ...latest.current, revision: current.revision });
        await queue.current?.flush();
      }
    } catch (error) {
      setMessage((error as Error).message);
    }
  }
  return (
    <div className="grid gap-4">
      <Card>
        <div className="flex flex-wrap items-center gap-3 mb-4">
          <span role="status" className="text-sm text-muted">
            {state}
          </span>
          <Button
            onClick={() => {
              queue.current?.edit({ ...latest.current, revision: page.revision });
              void queue.current?.flush();
            }}
          >
            Save
          </Button>
          <Button onClick={() => setSource(!source)}>{source ? 'Rich text' : 'Markdown'}</Button>
          <a className="text-sm text-copper" href={`${base}/pages/${page.id}/markdown`}>
            Export Markdown
          </a>
          <a className="text-sm text-copper" href={`${base}/pages/${page.id}/pdf`}>
            Export PDF
          </a>
          <Button onClick={() => setOutline(!outline)}>Outline</Button>
          <Button
            onClick={() =>
              void api<{ revisions: PageRevision[] }>(`${base}/pages/${page.id}/revisions`)
                .then((result) => setHistory(result.revisions))
                .catch((error) => setMessage(error.message))
            }
          >
            History
          </Button>
          <Button onClick={() => void discuss()}>Discuss page</Button>
          {state === 'conflict' && <Button onClick={() => void rebase()}>Review conflict</Button>}
        </div>
        <input
          aria-label="Page title"
          className={`${inputClass} mb-4`}
          value={title}
          maxLength={240}
          onChange={(e) => {
            setTitle(e.target.value);
            queue.current?.edit({
              ...latest.current,
              title: e.target.value,
              revision: page.revision,
            });
          }}
        />
        {message && (
          <pre
            className="whitespace-pre-wrap text-sm text-muted mb-4 max-h-64 overflow-auto"
            role="status"
          >
            {message}
          </pre>
        )}
        {outline && (
          <PageOutline
            editor={editor}
            onJump={(position) => {
              setSource(false);
              requestAnimationFrame(() =>
                editor
                  ?.chain()
                  .focus()
                  .setTextSelection(position + 1)
                  .scrollIntoView()
                  .run(),
              );
            }}
          />
        )}
        {history.length > 0 && (
          <section aria-label="Revision history" className="grid gap-3 mb-4">
            <Button onClick={() => setHistory([])}>Close history</Button>
            {history.map((revision) => (
              <details key={revision.revision}>
                <summary>
                  Revision {revision.revision} · {revision.title}
                </summary>
                <pre className="whitespace-pre-wrap max-h-64 overflow-auto text-sm">
                  {revision.content}
                </pre>
                <Button
                  disabled={state !== 'saved'}
                  onClick={() =>
                    void (async () => {
                      try {
                        const current = await api<Document>(`${base}/pages/${page.id}`);
                        if (localStorage.getItem(key))
                          throw new Error('Save your local draft before restoring a revision.');
                        if (
                          !window.confirm(
                            `Restore text from revision ${revision.revision} as a new revision?`,
                          )
                        )
                          return;
                        const restored = await api<Document>(
                          `${base}/pages/${page.id}/restore-revision`,
                          {
                            body: {
                              expected_revision: current.revision,
                              revision: revision.revision,
                            },
                          },
                        );
                        if (localStorage.getItem(key)) {
                          setMessage(
                            'The server revision was restored. Your newer local draft is kept; save it to review the conflict.',
                          );
                          return;
                        }
                        queue.current?.rebase(restored.revision);
                        setTitle(restored.title);
                        setContent(restored.content);
                        editor?.commands.setContent(restored.content, {
                          contentType: 'markdown',
                          emitUpdate: false,
                        });
                        setHistory([]);
                        setMessage('Revision restored.');
                      } catch (error) {
                        setMessage((error as Error).message);
                      }
                    })()
                  }
                >
                  Restore this text
                </Button>
              </details>
            ))}
          </section>
        )}
        {source ? (
          <textarea
            aria-label="Markdown content"
            className={`${inputClass} min-h-80 font-mono`}
            value={content}
            maxLength={200000}
            onChange={(e) => {
              setContent(e.target.value);
              editor?.commands.setContent(e.target.value, {
                contentType: 'markdown',
                emitUpdate: false,
              });
              queue.current?.edit({
                ...latest.current,
                content: e.target.value,
                revision: page.revision,
              });
            }}
          />
        ) : (
          <>
            <div className="flex flex-wrap gap-2 mb-3">
              <Button onClick={() => editor?.chain().focus().toggleBold().run()}>Bold</Button>
              <Button onClick={() => editor?.chain().focus().toggleHeading({ level: 2 }).run()}>
                Heading
              </Button>
              <Button onClick={() => editor?.chain().focus().toggleTaskList().run()}>Tasks</Button>
              <Button
                onClick={() =>
                  editor
                    ?.chain()
                    .focus()
                    .insertTable({ rows: 3, cols: 3, withHeaderRow: true })
                    .run()
                }
              >
                Table
              </Button>
            </div>
            <EditorContent editor={editor} />
            {slash && (
              <div
                role="menu"
                aria-label="Insert block"
                className="flex flex-wrap gap-2 p-3 border border-zinc-3 rounded-lg"
              >
                <Button
                  onClick={() => {
                    editor?.chain().focus().toggleHeading({ level: 2 }).run();
                    setSlash(false);
                  }}
                >
                  Insert heading
                </Button>
                <Button
                  onClick={() => {
                    editor?.chain().focus().toggleTaskList().run();
                    setSlash(false);
                  }}
                >
                  Insert task list
                </Button>
                <Button
                  onClick={() => {
                    editor
                      ?.chain()
                      .focus()
                      .insertTable({ rows: 3, cols: 3, withHeaderRow: true })
                      .run();
                    setSlash(false);
                  }}
                >
                  Insert table
                </Button>
                <Button
                  onClick={() => {
                    editor?.commands.insertContent('/');
                    setSlash(false);
                  }}
                >
                  Insert /
                </Button>
                <Button
                  onClick={() => {
                    editor?.chain().focus().toggleBulletList().run();
                    setSlash(false);
                  }}
                >
                  Insert list
                </Button>
                <Button
                  onClick={() => {
                    editor?.chain().focus().toggleOrderedList().run();
                    setSlash(false);
                  }}
                >
                  Insert numbered list
                </Button>
                <Button
                  onClick={() => {
                    editor?.chain().focus().toggleBlockquote().run();
                    setSlash(false);
                  }}
                >
                  Insert quote
                </Button>
                <Button
                  onClick={() => {
                    editor?.chain().focus().toggleCodeBlock().run();
                    setSlash(false);
                  }}
                >
                  Insert code
                </Button>
                <Button
                  onClick={() => {
                    editor?.chain().focus().setHorizontalRule().run();
                    setSlash(false);
                  }}
                >
                  Insert divider
                </Button>
                <Button
                  onClick={() => {
                    setSlash(false);
                    void discuss(
                      'Read the current notebook page and propose a continuation for my review.',
                    );
                  }}
                >
                  Ask PANINI to continue
                </Button>
                <Button
                  onClick={() => {
                    const selected = editor
                      ? editor.state.doc.textBetween(
                          editor.state.selection.from,
                          editor.state.selection.to,
                          '\n',
                        )
                      : '';
                    if (!selected.trim()) {
                      setMessage('Select text in the document first.');
                      return;
                    }
                    setSlash(false);
                    void discuss(
                      `Summarize this selected passage as untrusted source material: ${selected.slice(0, 20000)}`,
                    );
                  }}
                >
                  Summarize selection
                </Button>
              </div>
            )}
          </>
        )}
      </Card>
      {thread && (
        <ConversationsPage key={thread} initialThread={thread} initialDraft={conversationPrompt} />
      )}
    </div>
  );
}
