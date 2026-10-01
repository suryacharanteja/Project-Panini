import { createFileRoute, useNavigate } from '@tanstack/react-router';
import { useMemo } from 'react';
import { Filters } from '@/components/filters';
import { Results } from '@/components/results';
import { Working } from '@/components/working';
import { SearchBox } from '@/components/search-box';
import { Wordmark } from '@/components/wordmark';
import { clusterInOrder, type SortMode } from '@/lib/rank';
import { HOME_CANONICAL, SEARCH_ROBOTS } from '@/lib/seo';
import { isSourceId, isWindowId, type SourceId, type WindowId } from '@/lib/sources';
import { useAsk } from '@/lib/use-ask';
import { useStableOrder } from '@/lib/use-stable-order';

interface SearchParams {
  q: string;
  w?: WindowId;
  s?: string;
  sort?: SortMode;
}

function parseSources(s: string | undefined): SourceId[] | undefined {
  if (!s) return undefined;
  const ids = s.split(',').filter(isSourceId);
  return ids.length > 0 ? ids : undefined;
}

export const Route = createFileRoute('/results')({
  validateSearch: (raw: Record<string, unknown>): SearchParams => {
    const q = typeof raw.q === 'string' ? raw.q.slice(0, 300) : '';
    const out: SearchParams = { q };
    if (typeof raw.w === 'string' && isWindowId(raw.w)) out.w = raw.w;
    if (typeof raw.s === 'string' && raw.s) out.s = raw.s;
    if (raw.sort === 'newest') out.sort = 'newest';
    return out;
  },
  head: ({ match }) => ({
    meta: [
      {
        title: match.search.q
          ? `${match.search.q} · JevRev Search`
          : 'JevRev Search — Picks where to search. Ranks what comes back.',
      },
      { name: 'robots', content: SEARCH_ROBOTS },
    ],
    links: [{ rel: 'canonical', href: HOME_CANONICAL }],
  }),
  component: SearchPage,
});

function Header({ q }: { q: string }) {
  return (
    <header className="sticky top-0 z-10 border-b bg-background/95 backdrop-blur">
      <div className="relative mx-auto flex max-w-5xl flex-wrap items-center gap-x-4 gap-y-3 px-4 py-3">
        <Wordmark size="sm" />
        <div className="order-last w-full min-w-0 max-w-2xl sm:order-none sm:flex-1">
          <SearchBox initial={q} compact key={q} />
        </div>
      </div>
    </header>
  );
}

function SearchPage() {
  const params = Route.useSearch();
  const navigate = useNavigate({ from: '/results' });
  const explicitSources = parseSources(params.s);
  const state = useAsk({ q: params.q, w: params.w, s: explicitSources });

  const sort = params.sort ?? 'best';
  const ordered = useStableOrder(state.items, sort);
  const clusters = useMemo(() => clusterInOrder(ordered), [ordered]);

  const setWindow = (w: WindowId | undefined) => navigate({ search: (prev) => ({ ...prev, w }) });
  const setSources = (ids: SourceId[] | undefined) =>
    navigate({ search: (prev) => ({ ...prev, s: ids?.join(',') }) });
  const setSort = (mode: SortMode) =>
    navigate({
      search: (prev) => ({ ...prev, sort: mode === 'newest' ? mode : undefined }),
      resetScroll: false,
    });

  return (
    <>
      <Header q={params.q} />
      <main id="main-content" tabIndex={-1} className="mx-auto w-full max-w-5xl px-4 py-4">
        {!params.q.trim() && <p className="text-muted-foreground">Type something to search.</p>}

        {state.phase === 'error' && (
          <div className="rounded-lg border border-destructive/40 bg-destructive/5 p-4 text-sm">
            <p className="font-medium">Search failed</p>
            <p className="mt-1 text-muted-foreground">{state.message}</p>
          </div>
        )}

        {params.q.trim() && state.phase !== 'error' && (
          <div className="max-w-3xl">
            <div className="min-w-0">
              <Filters
                state={state}
                explicitWindow={params.w}
                explicitSources={explicitSources}
                onWindow={setWindow}
                onSources={setSources}
                onReset={() =>
                  navigate({ search: (prev) => ({ ...prev, w: undefined, s: undefined }) })
                }
              />
              <Working
                state={state}
                actions={
                  state.items.length > 0 && (
                    <div
                      className="flex shrink-0 items-center gap-2 whitespace-nowrap text-xs"
                      role="group"
                      aria-label="Sort results"
                    >
                      <button
                        aria-pressed={sort === 'best'}
                        className="py-0.5 font-medium text-muted-foreground hover:text-foreground focus-visible:outline-offset-4 aria-pressed:text-foreground"
                        onClick={() => setSort('best')}
                        type="button"
                      >
                        Best match
                      </button>
                      <span aria-hidden className="text-muted-foreground/40">
                        /
                      </span>
                      <button
                        aria-pressed={sort === 'newest'}
                        className="py-0.5 font-medium text-muted-foreground hover:text-foreground focus-visible:outline-offset-4 aria-pressed:text-foreground"
                        onClick={() => setSort('newest')}
                        type="button"
                      >
                        Newest
                      </button>
                    </div>
                  )
                }
              />
              <Results clusters={clusters} streaming={state.phase !== 'done'} />
            </div>
          </div>
        )}
      </main>
    </>
  );
}
