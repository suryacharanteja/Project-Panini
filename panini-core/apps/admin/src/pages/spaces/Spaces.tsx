import { EditorContent, useEditor } from '@tiptap/react';
import StarterKit from '@tiptap/starter-kit';
import { Markdown } from '@tiptap/markdown';
import { TableKit } from '@tiptap/extension-table';
import TaskList from '@tiptap/extension-task-list';
import TaskItem from '@tiptap/extension-task-item';
import { useEffect, useRef, useState } from 'react';
import { api, harness, useResource } from '../../api';
import { Button, Card, inputClass, Page } from '../../components/ui';
import { Autosave, type Draft, type SaveState } from './autosave';
import { ConversationsPage } from '../Conversations';
import './editor.css';

type Space = { id: string; name: string };
type Document = Draft & { id: string; space_id: string; parent_id: string | null };
const base = harness('/spaces');

function DocumentEditor({ page }: { page: Document }) {
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
          <Button
            onClick={() =>
              void api<{ thread_id: string }>(`${base}/pages/${page.id}/conversation`, {
                method: 'POST',
              })
                .then((result) => setThread(result.thread_id))
                .catch((error) => setMessage(error.message))
            }
          >
            Discuss page
          </Button>
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
              </div>
            )}
          </>
        )}
      </Card>
      {thread && <ConversationsPage key={thread} initialThread={thread} />}
    </div>
  );
}

export function SpacesPage() {
  const spaces = useResource<{ spaces: Space[] }>(base);
  const [spaceId, setSpaceId] = useState('');
  const [trash, setTrash] = useState(false);
  const [query, setQuery] = useState('');
  const pages = useResource<{ pages: Document[] }>(
    spaceId
      ? query.trim()
        ? `${base}/search?space_id=${spaceId}&q=${encodeURIComponent(query)}`
        : `${base}/${spaceId}/pages?deleted=${trash}`
      : null,
  );
  const [page, setPage] = useState<Document | null>(null);
  const [error, setError] = useState('');
  async function createSpace() {
    const name = window.prompt('Space name');
    if (!name?.trim()) return;
    try {
      const space = await api<Space>(base, { body: { name } });
      setSpaceId(space.id);
      spaces.reload();
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function newPage(parentId: string | null = null) {
    const title = window.prompt('Page title');
    if (!title?.trim()) return;
    try {
      const doc = await api<Document>(`${base}/pages`, {
        body: { space_id: spaceId, title, parent_id: parentId },
      });
      setPage(doc);
      pages.reload();
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function open(id: string) {
    try {
      setPage(await api<Document>(`${base}/pages/${id}`));
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function lifecycle(id: string, restore: boolean) {
    try {
      const current = await api<Document>(`${base}/pages/${id}?deleted=${restore}`);
      if (
        !restore &&
        !window.confirm('Move this page to trash? Subpages must be moved or deleted first.')
      )
        return;
      await api(`${base}/pages/${id}`, {
        method: 'PATCH',
        body: { expected_revision: current.revision, [restore ? 'restore' : 'trash']: true },
      });
      setPage(null);
      pages.reload();
    } catch (error) {
      setError((error as Error).message);
    }
  }
  function depth(doc: Document) {
    let count = 0,
      parent = doc.parent_id;
    const seen = new Set<string>();
    while (parent && !seen.has(parent) && count < 12) {
      seen.add(parent);
      count++;
      parent = pages.data?.pages.find((p) => p.id === parent)?.parent_id ?? null;
    }
    return count;
  }
  return (
    <Page title="Spaces" eyebrow="Your notebook">
      {(error || spaces.error || pages.error) && (
        <p role="alert" className="text-red-400">
          {error || spaces.error || pages.error}
        </p>
      )}
      <div className="flex flex-wrap gap-3 mb-5">
        <Button onClick={() => void createSpace()}>New space</Button>
        <select
          aria-label="Space"
          className={inputClass}
          value={spaceId}
          onChange={(e) => {
            setSpaceId(e.target.value);
            setPage(null);
          }}
        >
          <option value="">Choose a space</option>
          {spaces.data?.spaces.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name}
            </option>
          ))}
        </select>
        <Button disabled={!spaceId} onClick={() => void newPage()}>
          New page
        </Button>
        <Button disabled={!page || trash} onClick={() => void newPage(page?.id)}>
          New subpage
        </Button>
        <Button
          disabled={!spaceId}
          onClick={() => {
            setTrash(!trash);
            setQuery('');
            setPage(null);
          }}
        >
          {trash ? 'Active pages' : 'Trash'}
        </Button>
        <input
          aria-label="Search pages"
          className={inputClass}
          placeholder="Search this space…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          disabled={!spaceId || trash}
        />
      </div>
      <div className="grid gap-5 lg:grid-cols-[240px_1fr]">
        <Card>
          <nav aria-label="Pages" className="grid gap-2">
            {pages.data?.pages.map((p) => (
              <div
                key={p.id}
                className="flex items-center gap-1"
                style={{ paddingLeft: depth(p) * 12 }}
              >
                <button
                  className="text-left text-sm p-2 rounded hover:bg-zinc-3"
                  onClick={() => !trash && void open(p.id)}
                  disabled={trash}
                >
                  {p.title}
                </button>
                <button
                  className="ml-auto text-xs text-muted"
                  aria-label={`${trash ? 'Restore' : 'Trash'} ${p.title}`}
                  onClick={() => void lifecycle(p.id, trash)}
                >
                  {trash ? 'Restore' : 'Trash'}
                </button>
              </div>
            ))}
            {spaceId && !pages.data?.pages.length && (
              <p className="text-muted text-sm">No pages yet.</p>
            )}
          </nav>
        </Card>
        {page ? (
          <DocumentEditor key={page.id} page={page} />
        ) : (
          <Card>
            <p className="text-muted">Choose a page to start writing.</p>
          </Card>
        )}
      </div>
    </Page>
  );
}
