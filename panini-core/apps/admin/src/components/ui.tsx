import { createContext, type ReactNode, useCallback, useContext, useRef, useState } from 'react';

export function cx(...parts: (string | false | null | undefined)[]) {
  return parts.filter(Boolean).join(' ');
}

export function Page({
  title,
  deva,
  eyebrow,
  actions,
  children,
}: {
  title: string;
  deva?: string;
  eyebrow?: string;
  actions?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="mx-auto w-full max-w-6xl px-5 pb-16 pt-8 md:px-8">
      <header className="mb-6 flex flex-wrap items-end justify-between gap-4">
        <div>
          {eyebrow && (
            <div className="mono mb-2 text-[11px] uppercase tracking-[0.2em] text-copper-light/80">
              {eyebrow}
            </div>
          )}
          <h1 className="display-font text-3xl font-light tracking-tight text-slate">{title}</h1>
          {deva && <div className="deva mt-1 text-sm text-copper/40">{deva}</div>}
        </div>
        {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
      </header>
      {children}
    </div>
  );
}

export function Card({
  children,
  className,
  title,
  deva,
  actions,
  flash,
}: {
  children: ReactNode;
  className?: string;
  title?: ReactNode;
  deva?: string;
  actions?: ReactNode;
  flash?: boolean;
}) {
  return (
    <section className={cx('surface p-5', flash && 'flash', className)}>
      {(title || actions) && (
        <div className="mb-4 flex items-start justify-between gap-3">
          <div>
            {title && <h2 className="text-base font-medium text-slate">{title}</h2>}
            {deva && <div className="deva text-xs text-copper/35">{deva}</div>}
          </div>
          {actions && <div className="flex shrink-0 flex-wrap gap-2">{actions}</div>}
        </div>
      )}
      {children}
    </section>
  );
}

type ButtonProps = React.ButtonHTMLAttributes<HTMLButtonElement> & {
  tone?: 'primary' | 'quiet' | 'danger' | 'ghost';
  busy?: boolean;
};

export function Button({
  tone = 'quiet',
  busy,
  className,
  children,
  disabled,
  ...rest
}: ButtonProps) {
  const tones = {
    primary:
      'bg-gradient-to-br from-copper to-copper-light text-obsidian font-medium hover:brightness-110 border border-copper-light/40',
    quiet: 'bg-zinc-2 text-slate border border-line hover:border-copper/60 hover:text-copper-glow',
    danger: 'bg-transparent text-danger border border-danger/40 hover:bg-danger/10',
    ghost: 'bg-transparent text-muted border border-transparent hover:text-copper-glow',
  };
  return (
    <button
      type="button"
      {...rest}
      disabled={disabled || busy}
      className={cx(
        'inline-flex min-h-9 items-center justify-center gap-2 rounded-lg px-3.5 py-1.5 text-sm transition disabled:cursor-not-allowed disabled:opacity-50',
        tones[tone],
        className,
      )}
    >
      {busy && (
        <span className="size-3 animate-spin rounded-full border-2 border-current border-t-transparent" />
      )}
      {children}
    </button>
  );
}

export function Badge({
  children,
  tone = 'neutral',
}: {
  children: ReactNode;
  tone?: 'neutral' | 'ok' | 'warn' | 'error' | 'active' | 'copper';
}) {
  const tones = {
    neutral: 'bg-zinc-3 text-muted',
    ok: 'bg-gold/10 text-gold',
    warn: 'bg-amber/10 text-amber',
    error: 'bg-danger/10 text-danger',
    active: 'bg-amber/15 text-amber',
    copper: 'bg-copper/15 text-copper-glow',
  };
  return (
    <span
      className={cx(
        'mono inline-flex items-center rounded-full px-2 py-0.5 text-[11px]',
        tones[tone],
      )}
    >
      {children}
    </span>
  );
}

export function StateDot({ state }: { state: string }) {
  const color =
    state === 'ok'
      ? 'bg-gold'
      : state === 'error'
        ? 'bg-danger'
        : state === 'warning'
          ? 'border-2 border-amber bg-transparent'
          : state === 'running'
            ? 'bg-amber pulse-amber'
            : 'bg-faint';
  // Shape as well as colour: ready is solid gold, needs-a-look is a hollow amber ring.
  return <span className={cx('inline-block size-2.5 shrink-0 rounded-full', color)} aria-hidden />;
}

export function Field({
  label,
  hint,
  children,
}: {
  label: ReactNode;
  hint?: ReactNode;
  children: ReactNode;
}) {
  return (
    <label className="grid gap-1.5 text-sm">
      <span className="text-slate/90">{label}</span>
      {children}
      {hint && <span className="text-xs leading-relaxed text-faint">{hint}</span>}
    </label>
  );
}

export const inputClass =
  'w-full min-w-0 rounded-lg border border-line bg-obsidian px-3 py-2 text-sm text-slate placeholder:text-faint focus:border-copper/70 focus:outline-none';

export function Empty({ children }: { children: ReactNode }) {
  return <div className="py-6 text-center text-sm text-faint">{children}</div>;
}

export function Switch({
  checked,
  onChange,
  label,
}: {
  checked: boolean;
  onChange: (value: boolean) => void;
  label: string;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      onClick={() => onChange(!checked)}
      className={cx(
        'relative h-6 w-11 shrink-0 rounded-full border transition',
        checked ? 'border-copper bg-copper/40' : 'border-line bg-zinc-3',
      )}
    >
      <span
        className={cx(
          'absolute top-0.5 size-4.5 rounded-full transition-all',
          checked ? 'left-5.5 bg-copper-glow' : 'left-0.5 bg-faint',
        )}
      />
    </button>
  );
}

/* Toasts: calm confirmation for every change. */
type Toast = { id: number; text: string; tone: 'ok' | 'error' };
const ToastContext = createContext<(text: string, tone?: 'ok' | 'error') => void>(() => {});

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const next = useRef(1);
  const push = useCallback((text: string, tone: 'ok' | 'error' = 'ok') => {
    const id = next.current++;
    setToasts((all) => [...all, { id, text, tone }]);
    setTimeout(
      () => setToasts((all) => all.filter((t) => t.id !== id)),
      tone === 'error' ? 7000 : 3500,
    );
  }, []);
  return (
    <ToastContext.Provider value={push}>
      {children}
      <div
        className="pointer-events-none fixed bottom-5 right-5 z-50 grid gap-2"
        aria-live="polite"
      >
        {toasts.map((t) => (
          <div
            key={t.id}
            role={t.tone === 'error' ? 'alert' : 'status'}
            className={cx(
              'surface pointer-events-auto max-w-sm px-4 py-3 text-sm',
              t.tone === 'ok' ? 'flash text-slate' : 'border-danger/50 text-danger',
            )}
          >
            {t.text}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export const useToast = () => useContext(ToastContext);

/** Run an async action with a busy flag and a toast for the result. */
export function useAction() {
  const toast = useToast();
  const [busy, setBusy] = useState<string | null>(null);
  const run = useCallback(
    async (key: string, action: () => Promise<unknown>, success?: string) => {
      setBusy(key);
      try {
        await action();
        if (success) toast(success);
        return true;
      } catch (err) {
        toast((err as Error).message, 'error');
        return false;
      } finally {
        setBusy(null);
      }
    },
    [toast],
  );
  return { busy, run };
}
