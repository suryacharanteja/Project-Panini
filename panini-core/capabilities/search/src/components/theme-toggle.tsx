import { Moon, Sun } from 'lucide-react';
import { useEffect } from 'react';

export const THEME_SURFACE = { light: '#f4f6f8', dark: '#0b1420' } as const;

export function paintThemeColor(dark: boolean) {
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute('content', dark ? THEME_SURFACE.dark : THEME_SURFACE.light);
}

// Run before the page paints. Keep the preference on <html> even when storage is blocked.
export const themeScript = `(() => {
  const root = document.documentElement;
  let theme;
  try { theme = localStorage.getItem('jev-theme'); } catch {}
  if (theme === 'light' || theme === 'dark') root.dataset.theme = theme;
  const dark = theme === 'dark' || (theme !== 'light' && matchMedia('(prefers-color-scheme: dark)').matches);
  root.classList.toggle('dark', dark);
  const meta = document.querySelector && document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute('content', dark ? '${THEME_SURFACE.dark}' : '${THEME_SURFACE.light}');
})();`;

export function toggleTheme() {
  const root = document.documentElement;
  const dark = root.classList.toggle('dark');
  root.dataset.theme = dark ? 'dark' : 'light';
  paintThemeColor(dark);
  try {
    localStorage.setItem('jev-theme', dark ? 'dark' : 'light');
  } catch {
    /* Switching still works without storage. */
  }
}

export function ThemeToggle() {
  useEffect(() => {
    const system = matchMedia('(prefers-color-scheme: dark)');
    const syncSystem = () => {
      if (!document.documentElement.dataset.theme) {
        document.documentElement.classList.toggle('dark', system.matches);
        paintThemeColor(system.matches);
      }
    };
    syncSystem();
    system.addEventListener('change', syncSystem);
    return () => system.removeEventListener('change', syncSystem);
  }, []);

  return (
    <button
      className="inline-flex size-11 shrink-0 items-center justify-center rounded-full text-muted-foreground hover:bg-accent hover:text-foreground"
      onClick={toggleTheme}
      title="Toggle light / dark mode"
      type="button"
    >
      <Moon aria-hidden className="size-5 dark:hidden" />
      <Sun aria-hidden className="hidden size-5 dark:block" />
      <span className="sr-only dark:hidden">Switch to dark mode</span>
      <span className="sr-only hidden dark:block">Switch to light mode</span>
    </button>
  );
}
