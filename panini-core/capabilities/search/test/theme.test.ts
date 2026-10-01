import { runInNewContext } from 'node:vm';
import { describe, expect, it, vi } from 'vitest';
import {
  THEME_SURFACE,
  paintThemeColor,
  themeScript,
  toggleTheme,
} from '@/components/theme-toggle';

describe('theme before first paint', () => {
  it.each([
    { stored: null, systemDark: true, dark: true, preference: undefined },
    { stored: null, systemDark: false, dark: false, preference: undefined },
    { stored: 'light', systemDark: true, dark: false, preference: 'light' },
    { stored: 'dark', systemDark: false, dark: true, preference: 'dark' },
    { stored: 'invalid', systemDark: true, dark: true, preference: undefined },
    { stored: 'blocked', systemDark: true, dark: true, preference: undefined },
  ])('resolves $stored with systemDark=$systemDark', ({ stored, systemDark, dark, preference }) => {
    const classes = new Set<string>();
    const root = {
      dataset: {} as Record<string, string>,
      classList: {
        toggle(name: string, enabled: boolean) {
          if (enabled) classes.add(name);
          else classes.delete(name);
        },
      },
    };
    runInNewContext(themeScript, {
      document: { documentElement: root },
      localStorage: {
        getItem(key: string) {
          expect(key).toBe('jev-theme');
          if (stored === 'blocked') throw new Error('Storage unavailable');
          return stored;
        },
      },
      matchMedia(query: string) {
        expect(query).toBe('(prefers-color-scheme: dark)');
        return { matches: systemDark };
      },
    });
    expect(classes.has('dark')).toBe(dark);
    expect(root.dataset.theme).toBe(preference);
  });

  it('paints the status bar to match the resolved surface', () => {
    const meta = {
      content: '#ffffff',
      setAttribute(name: string, value: string) {
        if (name === 'content') this.content = value;
      },
    };
    const classes = new Set<string>();
    runInNewContext(themeScript, {
      document: {
        documentElement: {
          dataset: {} as Record<string, string>,
          classList: {
            toggle(_name: string, enabled: boolean) {
              if (enabled) classes.add('dark');
              else classes.delete('dark');
            },
            contains(name: string) {
              return classes.has(name);
            },
          },
        },
        querySelector(selector: string) {
          expect(selector).toBe('meta[name="theme-color"]');
          return meta;
        },
      },
      localStorage: { getItem: () => 'dark' },
      matchMedia: () => ({ matches: false }),
    });
    expect(meta.content).toBe('#0b1420');
  });
});

describe('theme-color after interaction', () => {
  it.each([false, true])(
    'toggles both ways and survives reload (storage blocked: %s)',
    (blocked) => {
      let dark = false;
      let stored: string | null = null;
      const root = {
        dataset: {} as Record<string, string>,
        classList: {
          toggle: (_name: string, force?: boolean) => (dark = force ?? !dark),
        },
      };
      const meta = { setAttribute: vi.fn() };
      const document = { documentElement: root, querySelector: () => meta };
      const localStorage = {
        getItem: () => stored,
        setItem: (_key: string, value: string) => {
          if (blocked) throw new Error('blocked');
          stored = value;
        },
      };
      vi.stubGlobal('document', document);
      vi.stubGlobal('localStorage', localStorage);
      try {
        toggleTheme();
        expect(dark).toBe(true);
        expect(root.dataset.theme).toBe('dark');
        expect(meta.setAttribute).toHaveBeenLastCalledWith('content', THEME_SURFACE.dark);
        if (!blocked) {
          dark = false;
          runInNewContext(themeScript, {
            document,
            localStorage,
            matchMedia: () => ({ matches: false }),
          });
          expect(dark).toBe(true);
        }
        toggleTheme();
        expect(dark).toBe(false);
        expect(root.dataset.theme).toBe('light');
        expect(meta.setAttribute).toHaveBeenLastCalledWith('content', THEME_SURFACE.light);
      } finally {
        vi.unstubAllGlobals();
      }
    },
  );
  it('writes the matching surface onto the existing meta tag', () => {
    const meta = { setAttribute: vi.fn() };
    vi.stubGlobal('document', { querySelector: () => meta });
    paintThemeColor(true);
    expect(meta.setAttribute).toHaveBeenCalledWith('content', THEME_SURFACE.dark);
    paintThemeColor(false);
    expect(meta.setAttribute).toHaveBeenCalledWith('content', THEME_SURFACE.light);
    vi.unstubAllGlobals();
  });
});
