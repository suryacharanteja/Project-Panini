import { Link, createFileRoute } from '@tanstack/react-router';
import { EngineStrip } from '@/components/home-demos';
import { SearchBox } from '@/components/search-box';
import { HOME_CANONICAL } from '@/lib/seo';

export const Route = createFileRoute('/')({
  head: () => ({
    meta: [{ property: 'og:url', content: HOME_CANONICAL }],
    links: [{ rel: 'canonical', href: HOME_CANONICAL }],
  }),
  component: Home,
});

const EXAMPLES = [
  'Direct flights from New York to London next Friday',
  'Machine learning engineer jobs in Dubai on Bayt and GulfTalent',
  'Remote frontend engineer roles on Wellfound this week',
  'Open-source multimodal models and datasets on Hugging Face',
  'Best open-source AI agents on GitHub',
  'Recent research papers on multimodal AI from Semantic Scholar',
  'Recent discussions about AI coding tools on Hacker News',
  'Technical deep-dives on AI agents from Substack and Medium',
  'Python tutorials for data visualization on YouTube',
  'Compare privacy-focused results from Brave and DuckDuckGo',
];

function Home() {
  return (
    <main
      id="main-content"
      tabIndex={-1}
      className="search-home mx-auto flex w-full max-w-3xl flex-1 flex-col items-center px-4 py-12 sm:py-16"
    >
      <img
        className="search-emblem"
        src="/search/favicon.png"
        width={88}
        height={88}
        alt="The Garuda eyes, JevRev Search emblem"
      />
      <p className="eyebrow mt-5">The eyes of JevRev</p>
      <h1 className="vt-wordmark display text-center text-[clamp(2.5rem,6vw,3.5rem)] leading-none tracking-[-0.01em]">
        JevRev <span className="text-primary">Search</span>
      </h1>
      <p className="mt-4 max-w-xl text-center text-base text-muted-foreground">
        A sharper view of what matters. Search across the web, work, travel, research and media —
        with ranked sources and linked evidence.
      </p>
      <div className="mt-7 w-full">
        <SearchBox autoFocus />
      </div>
      <p className="mt-7 w-full text-xs font-semibold uppercase tracking-wider text-muted-foreground">
        Start with a question
      </p>
      <ul aria-label="Example searches" className="mt-3 grid w-full gap-2 text-sm sm:grid-cols-2">
        {EXAMPLES.map((q) => (
          <li key={q}>
            <Link
              className="chip flex min-h-12 h-full items-center rounded-lg border bg-card px-4 py-3 text-muted-foreground hover:bg-accent hover:text-foreground"
              search={{ q }}
              to="/results"
              viewTransition
            >
              {q}
            </Link>
          </li>
        ))}
      </ul>
      <div className="mt-10">
        <EngineStrip />
      </div>
    </main>
  );
}
