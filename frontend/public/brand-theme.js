// Presentation only; shares the existing Search theme preference.
(() => {
  const root = document.documentElement;
  const system = matchMedia('(prefers-color-scheme: dark)');
  let preference;
  try {
    preference = localStorage.getItem('jev-theme');
  } catch {
    /* Storage can be disabled. */
  }
  const paint = (dark) => {
    root.classList.toggle('dark', dark);
    document
      .querySelector('meta[name="theme-color"]')
      ?.setAttribute('content', dark ? '#0b1420' : '#f4f6f8');
    document.querySelectorAll('[data-theme-toggle]').forEach((button) => {
      button.setAttribute('aria-label', dark ? 'Switch to light mode' : 'Switch to dark mode');
    });
  };
  if (preference === 'light' || preference === 'dark') root.dataset.theme = preference;
  const sync = () => paint(root.dataset.theme ? root.dataset.theme === 'dark' : system.matches);
  sync();
  system.addEventListener('change', sync);
  document.addEventListener('DOMContentLoaded', sync, { once: true });
  document.addEventListener('click', (event) => {
    if (!event.target.closest('[data-theme-toggle]')) return;
    root.dataset.theme = root.classList.contains('dark') ? 'light' : 'dark';
    try {
      localStorage.setItem('jev-theme', root.dataset.theme);
    } catch {
      /* Keep the in-page preference. */
    }
    sync();
  });
})();
