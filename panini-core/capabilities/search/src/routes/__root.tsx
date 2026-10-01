import { HeadContent, Link, Scripts, createRootRoute } from '@tanstack/react-router';
import type { ReactNode } from 'react';
import { BrandHeader, BrandFooter } from '@/components/brand-shell';
import { PwaRegister } from '@/components/pwa-register';
import { THEME_SURFACE, themeScript } from '@/components/theme-toggle';
import { SHARE_IMAGE } from '@/lib/seo';
import appCss from '../styles.css?url';

const TITLE = 'JevRev Search — Picks where to search. Ranks what comes back.';
const DESCRIPTION =
  'The eyes of JevRev. Ask a question, explore ranked web sources and follow the evidence with AI summaries.';
const SHARE_IMAGE_ALT = 'The golden Garuda eyes of JevRev Search.';

export const Route = createRootRoute({
  head: () => ({
    links: [
      { href: appCss, rel: 'stylesheet' },
      { href: '/brand.css', rel: 'stylesheet' },
      { href: '/search/favicon.ico', rel: 'icon', sizes: 'any' },
      { href: '/search/favicon.png', rel: 'icon', type: 'image/png', sizes: '128x128' },
      {
        href: '/search/apple-touch-icon.png?v=garuda',
        rel: 'apple-touch-icon',
        sizes: '180x180',
      },
      { href: '/search/manifest.webmanifest', rel: 'manifest' },
    ],
    meta: [
      { charSet: 'utf-8' },
      { content: 'width=device-width, initial-scale=1', name: 'viewport' },
      { title: TITLE },
      { name: 'description', content: DESCRIPTION },
      { name: 'mobile-web-app-capable', content: 'yes' },
      { name: 'apple-mobile-web-app-capable', content: 'yes' },
      { name: 'apple-mobile-web-app-title', content: 'JevRev Search' },
      { name: 'apple-mobile-web-app-status-bar-style', content: 'default' },
      { property: 'og:type', content: 'website' },
      { property: 'og:site_name', content: 'JevRev Search' },
      { property: 'og:title', content: TITLE },
      { property: 'og:description', content: DESCRIPTION },
      { property: 'og:image', content: SHARE_IMAGE },
      { property: 'og:image:type', content: 'image/png' },
      { property: 'og:image:width', content: '1024' },
      { property: 'og:image:height', content: '1024' },
      { property: 'og:image:alt', content: SHARE_IMAGE_ALT },
      { name: 'twitter:card', content: 'summary' },
      { name: 'twitter:title', content: TITLE },
      { name: 'twitter:description', content: DESCRIPTION },
      { name: 'twitter:image', content: SHARE_IMAGE },
      { name: 'twitter:image:alt', content: SHARE_IMAGE_ALT },
    ],
  }),
  notFoundComponent: NotFound,
  shellComponent: RootDocument,
});

function NotFound() {
  return (
    <main id="main-content" tabIndex={-1} className="mx-auto max-w-2xl px-4 py-24 text-center">
      <h1 className="text-2xl font-semibold">Nothing here</h1>
      <p className="mt-2 text-muted-foreground">That page does not exist.</p>
      <Link className="mt-6 inline-block text-link underline" to="/">
        Back to search
      </Link>
    </main>
  );
}

function RootDocument({ children }: { readonly children: ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <meta content={THEME_SURFACE.light} name="theme-color" />
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
        <HeadContent />
      </head>
      <body className="min-h-dvh flex flex-col">
        <BrandHeader />
        <div className="flex flex-1 flex-col">{children}</div>
        <BrandFooter />
        <Scripts />
        <PwaRegister />
      </body>
    </html>
  );
}
