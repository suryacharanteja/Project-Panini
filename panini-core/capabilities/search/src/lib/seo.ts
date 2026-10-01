/** Jev-Rev serves this app under /search. Copies live in `public/robots.txt` and `public/sitemap.xml`. */
export const SITE_ORIGIN = 'http://localhost:3000';
export const HOME_CANONICAL = `${SITE_ORIGIN}/search/`;
export const SITEMAP_URL = `${SITE_ORIGIN}/search/sitemap.xml`;
export const SHARE_IMAGE = `${SITE_ORIGIN}/search/og-home.png`;

/** Result URLs are queries, not documents. Allow crawling so the directive is visible. */
export const SEARCH_ROBOTS = 'noindex, follow';
