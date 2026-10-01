import { useState } from 'react';
import { formatPublicationAge } from '@/lib/freshness';
import type { Cluster, RankedItem } from '@/lib/rank';
import { sourceById } from '@/lib/sources';
import { cn } from '@/lib/utils';
import { SourceIcon } from './source-icon';

function displayUrl(url: string): string {
  try {
    const u = new URL(url);
    const id = u.searchParams.get('id') ?? u.searchParams.get('v');
    const path =
      u.pathname.replace(/\/$/, '') + (id ? `?${u.searchParams.has('id') ? 'id' : 'v'}=${id}` : '');
    return `${u.hostname.replace(/^www\./, '')}${path.length > 48 ? `${path.slice(0, 48)}…` : path}`;
  } catch {
    return url;
  }
}

function ResultRow({ item, minor }: { item: RankedItem; minor?: boolean }) {
  const age = formatPublicationAge(item);

  return (
    <article className={cn('group', minor ? 'pl-4 border-l' : '')}>
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        <span
          className="inline-flex size-4 shrink-0 items-center justify-center"
          title={sourceById(item.source).label}
        >
          <SourceIcon className="size-3.5" id={item.source} />
        </span>
        <span className="truncate">{displayUrl(item.url)}</span>
        {age && (
          <span className="shrink-0">
            · <time dateTime={item.publishedDate}>{age}</time>
          </span>
        )}
        {item.engines.length > 1 && (
          <span title={item.engines.join(' + ')}>· found by {item.engines.length} engines</span>
        )}
      </div>
      <a
        className={cn(
          'mt-0.5 block text-link visited:text-visited hover:underline',
          minor ? 'text-base' : 'text-lg',
        )}
        href={item.url}
        rel="noreferrer"
        target="_blank"
      >
        {item.title}
      </a>
      {!minor && item.snippet && (
        <p className="mt-0.5 text-sm text-muted-foreground line-clamp-2">{item.snippet}</p>
      )}
      <div className="mt-1 flex items-center gap-3 text-xs text-muted-foreground">
        <span
          className="inline-flex items-center gap-1"
          title="How sure Jev is that this result is about what you asked"
        >
          <span
            className={cn(
              'inline-block size-2 rounded-full',
              item.relevance >= 0.7
                ? 'bg-emerald-500'
                : item.relevance >= 0.4
                  ? 'bg-amber-500'
                  : 'bg-neutral-400',
            )}
          />
          {item.ranked ? `${Math.round(item.relevance * 100)}% on topic` : 'Not ranked'}
        </span>
      </div>
    </article>
  );
}

/** Below this the judge says "not about what you asked"; such rows are folded away, not deleted. */
export const OFF_TOPIC = 0.3;

export function Results({ clusters, streaming }: { clusters: Cluster[]; streaming: boolean }) {
  const [showOffTopic, setShowOffTopic] = useState(false);
  const onTopic = clusters.filter((c) => !c.lead.ranked || c.lead.relevance >= OFF_TOPIC);
  const offTopic = clusters.filter((c) => c.lead.ranked && c.lead.relevance < OFF_TOPIC);

  if (clusters.length === 0) {
    if (streaming) return null;
    return (
      <p className="mt-8 text-muted-foreground">
        Nothing found. Try a wider time range, or more sources.
      </p>
    );
  }

  const render = (list: Cluster[]) =>
    list.map((cluster) => (
      <li className="enter flex flex-col gap-2" key={cluster.lead.id}>
        <ResultRow item={cluster.lead} />
        {cluster.others.map((item) => (
          <ResultRow item={item} key={item.id} minor />
        ))}
      </li>
    ));

  return (
    <>
      <ol className="mt-6 flex flex-col gap-6">{render(onTopic)}</ol>
      {offTopic.length > 0 && !streaming && (
        <div className="mt-8">
          <button
            className="text-sm text-muted-foreground underline-offset-4 hover:underline"
            onClick={() => setShowOffTopic((v) => !v)}
            type="button"
          >
            {showOffTopic ? 'Hide' : 'Show'} {offTopic.length} more that didn't seem to match
          </button>
          {showOffTopic && (
            <ol className="mt-4 flex flex-col gap-6 opacity-70">{render(offTopic)}</ol>
          )}
        </div>
      )}
    </>
  );
}
