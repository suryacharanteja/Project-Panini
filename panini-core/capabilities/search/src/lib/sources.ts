export type SourceId =
  | 'web'
  | 'hackernews'
  | 'reddit'
  | 'github'
  | 'x'
  | 'arxiv'
  | 'youtube'
  | 'wikipedia'
  | 'imdb'
  | 'wechat'
  | 'jobs'
  | 'flights'
  | 'yahoo'
  | 'brave'
  | 'aianswers'
  | 'huggingface'
  | 'linkedin'
  | 'naukrigulf'
  | 'bayt'
  | 'gulftalent'
  | 'indeeduae'
  | 'wellfound'
  | 'talentnetworks'
  | 'scholar'
  | 'editorial';

/** A retrieval lane; platform sources restrict the same web provider by domain. */
export interface Lane {
  service: string;
  site?: string;
  /** False for engines that reject or ignore `time_range` (wikipedia, imdb, wechat). */
  timeFilter?: boolean;
  /** True for catalogue engines that want a name or title, not a sentence (imdb). */
  entityQuery?: boolean;
}

export interface Source {
  id: SourceId;
  label: string;
  lanes: Lane[];
  /** Plain-language description handed to the judge. */
  description: string;
  /** Yes/no question the judge answers to decide whether this source is wanted. */
  ask: { question: string; yes: string; no: string };
  /** Searched when the request does not single out any source. */
  defaultOn: boolean;
}

export interface SourceGroup {
  id: string;
  label: string;
  sourceIds: readonly SourceId[];
}

export const SOURCES: readonly Source[] = [
  {
    id: 'web',
    label: 'Web',
    lanes: [{ service: 'openai' }],
    description: 'Web results: news sites, blogs, documentation, anything on the open web',
    ask: {
      question: 'Would general web pages (news, articles, blogs, docs) help answer this request?',
      yes: 'The request is a general question, or asks for news, articles, coverage, docs or blog posts',
      no: 'The request only makes sense on a specific platform such as Reddit, GitHub, arXiv, YouTube or IMDb',
    },
    defaultOn: true,
  },
  {
    id: 'yahoo',
    label: 'Yahoo',
    lanes: [{ service: 'openai', site: 'search.yahoo.com' }],
    description: 'Yahoo Search index results',
    ask: {
      question: 'Would Yahoo Search results be specifically useful for this request?',
      yes: 'The request names Yahoo or asks to compare or search Yahoo results',
      no: 'The request does not specifically mention Yahoo Search',
    },
    defaultOn: false,
  },
  {
    id: 'brave',
    label: 'Brave & DuckDuckGo',
    lanes: [
      { service: 'openai', site: 'search.brave.com' },
      { service: 'openai', site: 'duckduckgo.com' },
    ],
    description: 'Privacy-focused search results from Brave Search and DuckDuckGo',
    ask: {
      question: 'Would privacy-focused Brave Search or DuckDuckGo results be specifically useful?',
      yes: 'The request names Brave, DuckDuckGo, privacy-first search, or asks to compare those engines',
      no: 'The request does not specifically ask for Brave or DuckDuckGo',
    },
    defaultOn: false,
  },
  {
    id: 'aianswers',
    label: 'Perplexity & ChatGPT',
    lanes: [
      { service: 'openai', site: 'perplexity.ai' },
      { service: 'openai', site: 'chatgpt.com' },
    ],
    description: 'AI-native answer engines with cited web intelligence',
    ask: {
      question:
        'Would AI-native answer engines such as Perplexity or ChatGPT Search be specifically useful?',
      yes: 'The request names Perplexity, ChatGPT Search, AI answer engines, or asks to compare synthesized answers',
      no: 'The request does not specifically ask for an AI-native answer engine',
    },
    defaultOn: false,
  },
  {
    id: 'hackernews',
    label: 'Hacker News',
    lanes: [{ service: 'openai', site: 'news.ycombinator.com' }],
    description: 'Hacker News threads and comments',
    ask: {
      question: 'Would Hacker News threads fit this request?',
      yes: 'The request names Hacker News or HN, or asks what developers or the tech community are saying, their reactions, opinions or discussion about a technical topic',
      no: 'The request is a factual lookup, or is about something outside technology and startups',
    },
    defaultOn: false,
  },
  {
    id: 'reddit',
    label: 'Reddit',
    lanes: [{ service: 'openai', site: 'reddit.com' }],
    description: 'Reddit posts and comment threads',
    ask: {
      question: 'Would Reddit threads fit this request?',
      yes: 'The request names Reddit or a subreddit, or asks what people are saying, their experiences, recommendations, opinions or discussion',
      no: 'The request is a factual lookup or asks for official sources, code, papers or videos',
    },
    defaultOn: false,
  },
  {
    id: 'github',
    label: 'GitHub',
    lanes: [{ service: 'openai', site: 'github.com' }],
    description: 'GitHub repositories, issues, pull requests and releases',
    ask: {
      question:
        'Is the user looking for code: repositories, releases, issues, pull requests or open source projects?',
      yes: 'The request names GitHub, or asks for repos, libraries, releases, issues, PRs, or open source tools',
      no: 'The request is about discussion, news or opinions rather than code',
    },
    defaultOn: false,
  },
  {
    id: 'huggingface',
    label: 'Hugging Face',
    lanes: [{ service: 'openai', site: 'huggingface.co' }],
    description: 'Open-source AI models, datasets, spaces and agent frameworks',
    ask: {
      question: 'Is the user looking for Hugging Face models, datasets, spaces or AI frameworks?',
      yes: 'The request mentions Hugging Face, models, datasets, model cards, vector databases or agent frameworks',
      no: 'The request is not about Hugging Face or its AI ecosystem',
    },
    defaultOn: false,
  },
  {
    id: 'jobs',
    label: 'All Jobs',
    lanes: [
      { service: 'openai', site: 'linkedin.com/jobs' },
      { service: 'openai', site: 'indeed.com' },
      { service: 'openai', site: 'wellfound.com' },
      { service: 'openai', site: 'naukrigulf.com' },
      { service: 'openai', site: 'bayt.com' },
      { service: 'openai', site: 'gulftalent.com' },
      { service: 'openai', site: 'toptal.com' },
    ],
    description: 'Aggregated global and regional job listings across career portals',
    ask: {
      question:
        'Is the user looking for job openings, roles, companies that are hiring, or career opportunities?',
      yes: 'The request mentions jobs, hiring, careers, openings, roles, internships, remote work, a job title, employer or location',
      no: 'The request is not looking for an employment opportunity',
    },
    defaultOn: false,
  },
  {
    id: 'linkedin',
    label: 'LinkedIn',
    lanes: [{ service: 'openai', site: 'linkedin.com/jobs' }],
    description: 'Global professional networking and career listings',
    ask: {
      question:
        'Is the user specifically looking for LinkedIn jobs or professional-networking information?',
      yes: 'The request names LinkedIn, professional networking or LinkedIn job listings',
      no: 'The request does not specifically require LinkedIn',
    },
    defaultOn: false,
  },
  {
    id: 'naukrigulf',
    label: 'Naukri Gulf',
    lanes: [{ service: 'openai', site: 'naukrigulf.com' }],
    description: 'Middle East career listings and regional roles',
    ask: {
      question: 'Is the user looking for jobs through Naukri Gulf or in the Middle East?',
      yes: 'The request names Naukri Gulf, GCC, Middle East, UAE, Saudi Arabia or regional employment',
      no: 'The request does not specifically require Naukri Gulf or Middle East listings',
    },
    defaultOn: false,
  },
  {
    id: 'bayt',
    label: 'Bayt.com',
    lanes: [{ service: 'openai', site: 'bayt.com' }],
    description: 'Managerial and technical jobs across the UAE and Middle East',
    ask: {
      question: 'Is the user looking for jobs through Bayt.com or in the UAE and Middle East?',
      yes: 'The request names Bayt, UAE, Dubai, Abu Dhabi, Middle East, managerial or technical regional roles',
      no: 'The request does not specifically require Bayt.com listings',
    },
    defaultOn: false,
  },
  {
    id: 'gulftalent',
    label: 'GulfTalent',
    lanes: [{ service: 'openai', site: 'gulftalent.com' }],
    description: 'GCC opportunities from multinational employers and recruiters',
    ask: {
      question: 'Is the user looking for jobs through GulfTalent or GCC employment opportunities?',
      yes: 'The request names GulfTalent, GCC, multinational employers or recruiter-led regional opportunities',
      no: 'The request does not specifically require GulfTalent listings',
    },
    defaultOn: false,
  },
  {
    id: 'indeeduae',
    label: 'Indeed UAE',
    lanes: [{ service: 'openai', site: 'ae.indeed.com' }],
    description: 'UAE jobs from local and multinational employers',
    ask: {
      question: 'Is the user looking for UAE roles specifically on Indeed?',
      yes: 'The request names Indeed UAE, Indeed, UAE, Dubai or Abu Dhabi job listings',
      no: 'The request does not specifically require Indeed UAE',
    },
    defaultOn: false,
  },
  {
    id: 'wellfound',
    label: 'Wellfound',
    lanes: [{ service: 'openai', site: 'wellfound.com' }],
    description: 'Startup and venture-backed technology roles',
    ask: {
      question: 'Is the user looking for startup or venture-backed jobs through Wellfound?',
      yes: 'The request names Wellfound, AngelList, startups, venture-backed companies or scale-ups',
      no: 'The request does not specifically require Wellfound',
    },
    defaultOn: false,
  },
  {
    id: 'talentnetworks',
    label: 'Honeypot & Toptal',
    lanes: [
      { service: 'openai', site: 'honeypot.io' },
      { service: 'openai', site: 'toptal.com' },
    ],
    description: 'Vetted technical talent and engineering leadership networks',
    ask: {
      question:
        'Is the user looking for vetted technical talent, freelance specialists or engineering leadership roles?',
      yes: 'The request names Honeypot, Toptal, vetted talent, freelance technical work or engineering leadership',
      no: 'The request does not specifically require Honeypot or Toptal',
    },
    defaultOn: false,
  },
  {
    id: 'flights',
    label: 'Travel & Flights',
    lanes: [
      { service: 'openai', site: 'google.com/travel/flights' },
      { service: 'openai', site: 'flightaware.com' },
      { service: 'openai', site: 'skyscanner.com' },
    ],
    description: 'Flight routes, schedules, status and fare information',
    ask: {
      question:
        'Is the user looking for flight options, flight status, airfares, routes, schedules, airports or airlines?',
      yes: 'The request mentions a flight, airline, airport, route, fare, travel date, departure, arrival or flight status',
      no: 'The request is not about air travel or flight information',
    },
    defaultOn: false,
  },
  {
    id: 'x',
    label: 'X',
    lanes: [{ service: 'openai', site: 'x.com' }],
    description: 'Posts on X (formerly Twitter)',
    ask: {
      question: 'Would posts on X (Twitter) fit this request?',
      yes: 'The request names X, Twitter or tweets, or asks what people are saying, their reactions, opinions or discussion about a product, launch, announcement, company or person, especially in tech and startups; launches and news break on X first',
      no: 'The request is a factual lookup, or asks for long-form content such as tutorials, papers or documentation',
    },
    defaultOn: false,
  },
  {
    id: 'arxiv',
    label: 'arXiv',
    lanes: [{ service: 'openai', site: 'arxiv.org' }],
    description: 'Academic papers and preprints on arXiv',
    ask: {
      question: 'Is the user asking for academic papers, research or preprints?',
      yes: 'The request mentions papers, research, arXiv, studies or preprints',
      no: 'The request is not about academic research',
    },
    defaultOn: false,
  },
  {
    id: 'scholar',
    label: 'Semantic & Google Scholar',
    lanes: [
      { service: 'openai', site: 'semanticscholar.org' },
      { service: 'openai', site: 'scholar.google.com' },
    ],
    description: 'Scholarly articles, whitepapers and academic citations',
    ask: {
      question:
        'Is the user looking for scholarly articles, citations, whitepapers or Google Scholar and Semantic Scholar results?',
      yes: 'The request names Semantic Scholar, Google Scholar, citations, scholarly articles, whitepapers or peer-reviewed work',
      no: 'The request is not specifically looking for scholarly indexes',
    },
    defaultOn: false,
  },
  {
    id: 'wikipedia',
    label: 'Wikipedia',
    lanes: [{ service: 'openai', site: 'wikipedia.org' }],
    description: 'Encyclopedia articles on Wikipedia',
    ask: {
      question: 'Is the user asking for encyclopedic facts, definitions, background or history?',
      yes: 'The request asks what or who something is, how it works, its history or background facts',
      no: 'The request asks for opinions, news, recent events, code, papers or videos',
    },
    defaultOn: false,
  },
  {
    id: 'imdb',
    label: 'IMDb',
    lanes: [{ service: 'openai', site: 'imdb.com', entityQuery: true }],
    description: 'Movies, TV shows, actors and directors on IMDb',
    ask: {
      question:
        'Is the user asking about a film, TV series, actor, director or other screen credit?',
      yes: 'The request names or describes a movie or show, or asks who acted in, directed or made one',
      no: 'The request is not about film or television',
    },
    defaultOn: false,
  },
  {
    id: 'wechat',
    label: 'WeChat',
    lanes: [{ service: 'openai', site: 'mp.weixin.qq.com' }],
    description: 'Articles from WeChat official accounts (微信公众号)',
    ask: {
      question:
        'Would Chinese-language articles from WeChat official accounts (微信公众号) fit this request?',
      yes: 'The request mentions 微信, 公众号 or WeChat, or is written in Chinese and asks for articles, tutorials, analysis or opinions',
      no: 'The request is not in Chinese and does not mention WeChat',
    },
    defaultOn: false,
  },
  {
    id: 'editorial',
    label: 'Substack & Medium',
    lanes: [
      { service: 'openai', site: 'substack.com' },
      { service: 'openai', site: 'medium.com' },
    ],
    description: 'Editorial deep-dives, technical breakdowns and industry essays',
    ask: {
      question: 'Would editorial writing from Substack or Medium fit this request?',
      yes: 'The request names Substack, Medium, newsletters, essays, deep-dives, technical breakdowns or industry analysis',
      no: 'The request does not specifically ask for Substack or Medium',
    },
    defaultOn: false,
  },
  {
    id: 'youtube',
    label: 'YouTube',
    lanes: [{ service: 'openai', site: 'youtube.com' }],
    description: 'Videos on YouTube',
    ask: {
      question: 'Is the user asking for videos?',
      yes: 'The request mentions videos, YouTube, talks, tutorials to watch, or channels',
      no: 'The request is not about video content',
    },
    defaultOn: false,
  },
];

export const SOURCE_GROUPS: readonly SourceGroup[] = [
  { id: 'core', label: 'Core search', sourceIds: ['web', 'yahoo', 'brave', 'aianswers'] },
  { id: 'code', label: 'Developer & code', sourceIds: ['github', 'huggingface'] },
  {
    id: 'careers',
    label: 'Career portals',
    sourceIds: [
      'jobs',
      'linkedin',
      'naukrigulf',
      'bayt',
      'gulftalent',
      'indeeduae',
      'wellfound',
      'talentnetworks',
    ],
  },
  { id: 'research', label: 'Research', sourceIds: ['arxiv', 'scholar', 'wikipedia'] },
  {
    id: 'community',
    label: 'Community & editorial',
    sourceIds: ['reddit', 'x', 'editorial', 'hackernews'],
  },
  { id: 'travel', label: 'Travel & logistics', sourceIds: ['flights'] },
  { id: 'media', label: 'Media & regional', sourceIds: ['imdb', 'youtube', 'wechat'] },
];

export const DEFAULT_SOURCE_IDS = SOURCES.filter((s) => s.defaultOn).map((s) => s.id);

export const SOURCE_IDS = SOURCES.map((s) => s.id) as readonly SourceId[];

export function isSourceId(value: string): value is SourceId {
  return (SOURCE_IDS as readonly string[]).includes(value);
}

export function sourceById(id: SourceId): Source {
  const found = SOURCES.find((s) => s.id === id);
  if (!found) throw new Error(`Unknown source: ${id}`);
  return found;
}

export type WindowId = 'any' | '24h' | '7d' | '30d';

export interface Window {
  id: WindowId;
  label: string;
  /** Infinity for no limit. */
  hours: number;
  /** Requested recency, applied to retrieval and known publication dates. */
  timeRange?: 'day' | 'week' | 'month';
  description: string;
}

export const WINDOWS: readonly Window[] = [
  {
    id: 'any',
    label: 'Any time',
    hours: Number.POSITIVE_INFINITY,
    description: 'The request does not ask for recent results; older, evergreen pages are fine',
  },
  {
    id: '24h',
    label: 'Past 24 hours',
    hours: 24,
    timeRange: 'day',
    description: 'Only things from today or the last day',
  },
  {
    id: '7d',
    label: 'Past week',
    hours: 24 * 7,
    timeRange: 'week',
    description: 'Things from the last several days, up to a week',
  },
  {
    id: '30d',
    label: 'Past month',
    hours: 24 * 30,
    timeRange: 'month',
    description: 'Things from the last few weeks, up to a month',
  },
];

export const DEFAULT_WINDOW: WindowId = 'any';

export function isWindowId(value: string): value is WindowId {
  return WINDOWS.some((w) => w.id === value);
}

export function windowById(id: WindowId): Window {
  const found = WINDOWS.find((w) => w.id === id);
  if (!found) throw new Error(`Unknown window: ${id}`);
  return found;
}
