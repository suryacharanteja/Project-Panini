import { useEffect, useState } from 'react';
import { useNavigate, useParams } from '@tanstack/react-router';
import { api, harness, useResource } from '../../api';
import { Button, Card, inputClass, Page } from '../../components/ui';
import { PageDocument } from './PageDocument';
import { SpaceLibrary } from './SpaceLibrary';
import type { Document, PageSummary, Space } from './types';
const base = harness('/spaces');

export function SpacesPage() {
  const params = useParams({ strict: false }) as { spaceId?: string; pageId?: string };
  const navigate = useNavigate();
  const spaces = useResource<{ spaces: Space[] }>(base);
  const [spaceId, setSpaceId] = useState(params.spaceId || '');
  const [trash, setTrash] = useState(false);
  const [query, setQuery] = useState('');
  const pages = useResource<{ pages: PageSummary[] }>(
    spaceId
      ? query.trim()
        ? `${base}/search?space_id=${spaceId}&q=${encodeURIComponent(query)}`
        : `${base}/${spaceId}/pages?deleted=${trash}`
      : null,
  );
  const [page, setPage] = useState<Document | null>(null);
  const [error, setError] = useState('');
  const [grid, setGrid] = useState(false);
  const [parent, setParent] = useState('');
  const [access, setAccess] = useState<{ persona_id: string }[] | null>(null);
  const [persona, setPersona] = useState('');
  useEffect(() => {
    if (!params.spaceId) return;
    let alive = true;
    setSpaceId(params.spaceId);
    if (params.pageId)
      void api<Document>(`${base}/pages/${params.pageId}`)
        .then((document) => {
          if (!alive) return;
          if (document.space_id !== params.spaceId)
            throw new Error('This page is in another space.');
          setPage(document);
        })
        .catch((error) => {
          if (alive) setError(error.message);
        });
    return () => {
      alive = false;
    };
  }, [params.spaceId, params.pageId]);
  async function move() {
    if (!page) return;
    try {
      if (localStorage.getItem(`panini.page.draft.${page.id}`))
        throw new Error('Save your draft before moving this page.');
      const current = await api<Document>(`${base}/pages/${page.id}`);
      const moved = await api<Document>(`${base}/pages/${page.id}`, {
        method: 'PATCH',
        body: { expected_revision: current.revision, move: true, parent_id: parent || null },
      });
      setPage(moved);
      pages.reload();
    } catch (error) {
      setError((error as Error).message);
    }
  }
  async function permissions(personaId?: string, allowed?: boolean) {
    try {
      if (personaId)
        await api(`${base}/access/${spaceId}`, { body: { persona_id: personaId, allowed } });
      const result = await api<{ personas: { persona_id: string }[] }>(`${base}/access/${spaceId}`);
      setAccess(result.personas);
    } catch (error) {
      setError((error as Error).message);
    }
  }
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
      const document = await api<Document>(`${base}/pages/${id}`);
      setPage(document);
      void navigate({
        to: '/spaces/$spaceId/pages/$pageId',
        params: { spaceId: document.space_id, pageId: id },
      });
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
  async function destroyPage(id: string) {
    try {
      const current = await api<Document>(`${base}/pages/${id}?deleted=true`);
      const confirmation = window.prompt(
        `Permanently delete this page and its revisions? Type the exact title: ${current.title}`,
      );
      if (confirmation === null) return;
      const request = await api<{ id: string; nonce: string; action_digest: string }>(
        `${base}/pages/${id}/destroy-request`,
        { body: { expected_revision: current.revision, confirmation } },
      );
      const approved = window.confirm(
        `Permanently delete ${current.title}? This cannot be undone. Earlier conversation quotes and receipts remain in their original records.`,
      );
      await api(`${base}/destroy/${request.id}/confirm`, {
        body: { nonce: request.nonce, action_digest: request.action_digest, approved },
      });
      pages.reload();
    } catch (error) {
      setError((error as Error).message);
    }
  }
  return (
    <Page title="Spaces" eyebrow="Your notebook">
      {(error || spaces.error || pages.error) && (
        <p role="alert" className="text-red-400">
          {error || spaces.error || pages.error}
        </p>
      )}
      <div className="flex flex-wrap gap-3 mb-5">
        <Button onClick={() => setGrid(!grid)}>{grid ? 'List view' : 'Grid view'}</Button>
        <Button disabled={!spaceId} onClick={() => void permissions()}>
          Space access
        </Button>
        <Button onClick={() => void createSpace()}>New space</Button>
        <select
          aria-label="Space"
          className={inputClass}
          value={spaceId}
          onChange={(e) => {
            setSpaceId(e.target.value);
            setPage(null);
            setAccess(null);
            void navigate({
              to: e.target.value ? '/spaces/$spaceId' : '/spaces',
              params: { spaceId: e.target.value },
            });
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
      {access && (
        <Card title="Space access">
          <p className="text-sm text-muted mb-3">
            The owner always has access. Grant access by persona ID; revoked personas lose access
            immediately.
          </p>
          <p className="text-sm text-muted mb-3 break-all">Space ID: {spaceId}</p>
          <div className="flex flex-wrap gap-2">
            <input
              aria-label="Persona ID"
              className={inputClass}
              value={persona}
              onChange={(event) => setPersona(event.target.value)}
            />
            <Button
              disabled={!persona.trim()}
              onClick={() => void permissions(persona.trim(), true)}
            >
              Grant access
            </Button>
            <Button onClick={() => setAccess(null)}>Close access</Button>
          </div>
          {access.map((item) => (
            <div key={item.persona_id} className="flex gap-3 mt-2">
              <span>{item.persona_id}</span>
              <Button onClick={() => void permissions(item.persona_id, false)}>
                Revoke access
              </Button>
            </div>
          ))}
        </Card>
      )}
      {page && (
        <div className="flex flex-wrap gap-2 mb-4 mt-4">
          <select
            aria-label="Move under page"
            className={inputClass}
            value={parent}
            onChange={(event) => setParent(event.target.value)}
          >
            <option value="">Top level</option>
            {pages.data?.pages
              .filter((item) => item.id !== page.id)
              .map((item) => (
                <option key={item.id} value={item.id}>
                  {item.title}
                </option>
              ))}
          </select>
          <Button onClick={() => void move()}>Move page</Button>
        </div>
      )}
      <div
        className={
          grid
            ? 'grid gap-5 lg:grid-cols-[minmax(280px,400px)_1fr]'
            : 'grid gap-5 lg:grid-cols-[240px_1fr]'
        }
      >
        <SpaceLibrary
          pages={pages.data?.pages || []}
          spaceId={spaceId}
          trash={trash}
          grid={grid}
          onOpen={open}
          onLifecycle={lifecycle}
          onDestroy={destroyPage}
        />
        {page ? (
          <PageDocument key={`${page.id}:${page.revision}`} page={page} />
        ) : (
          <Card>
            <p className="text-muted">Choose a page to start writing.</p>
          </Card>
        )}
      </div>
    </Page>
  );
}
