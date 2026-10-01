import { ThemeToggle } from './theme-toggle';

export function BrandHeader() {
  return (
    <>
      <a className="skip-link" href="#main-content">
        Skip to content
      </a>
      <header className="site-header">
        <div className="header-inner">
          <a className="brand-home" href="/" aria-label="JevRev home">
            <img src="/brand/jevrev.png" alt="" width={42} height={42} />
            <div>
              <span className="brand-word">
                Jev<span>Rev</span>
              </span>
              <span className="brand-caption">The Garuda ecosystem</span>
            </div>
          </a>
          <nav className="site-nav" aria-label="Main navigation">
            <a href="/">
              <img src="/brand/jevrev.png" alt="" width={24} height={24} />
              Compare
            </a>
            <a href="/browser">
              <img src="/brand/browser.png" alt="" width={24} height={24} />
              Browser
            </a>
            <a href="/search" aria-current="page">
              <img src="/brand/search.png" alt="" width={24} height={24} />
              Search
            </a>
          </nav>
          <div className="header-tools">
            <span className="brand-motto">Speed. Precision. Control.</span>
            <ThemeToggle />
          </div>
        </div>
      </header>
    </>
  );
}

export function BrandFooter() {
  return (
    <footer className="site-footer">
      <div className="footer-inner">
        <div className="footer-story">
          <p>
            <strong>Inspired by Garuda. Built to move ideas forward.</strong>JevRev brings
            structured intelligence, browser action and focused search into one workspace. Wings to
            explore. Eyes to discover. A claw to take action — with you in control.
          </p>
          <nav className="ecosystem" aria-label="Explore JevRev">
            <a href="/browser">
              <img src="/brand/browser.png" alt="" width={28} height={28} />
              Browser
            </a>
            <a href="/search">
              <img src="/brand/search.png" alt="" width={28} height={28} />
              Search
            </a>
          </nav>
        </div>
        <div className="footer-bottom">
          <span>© 2026 JevRev. All rights reserved.</span>
          <span>
            Designed and developed by <strong>CharanTheAiGuy</strong> ·{' '}
            <a href="https://ndimensions.ai" target="_blank" rel="noopener noreferrer">
              nDimensions.AI ↗
            </a>
          </span>
        </div>
      </div>
    </footer>
  );
}
