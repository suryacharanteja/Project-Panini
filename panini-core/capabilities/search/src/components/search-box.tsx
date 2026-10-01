import { useNavigate } from '@tanstack/react-router';
import { SearchIcon } from 'lucide-react';
import { useState } from 'react';
import { Input } from '@/components/ui/input';
import { cn } from '@/lib/utils';

export function SearchBox({
  initial = '',
  compact = false,
  autoFocus = false,
}: {
  initial?: string;
  compact?: boolean;
  autoFocus?: boolean;
}) {
  const [value, setValue] = useState(initial);
  const navigate = useNavigate();

  return (
    <form
      className="vt-searchbox relative"
      onSubmit={(event) => {
        event.preventDefault();
        const q = value.trim();
        if (!q) return;
        // A new request resets explicit filters so the judge decides again.
        navigate({ to: '/results', search: { q }, viewTransition: true });
      }}
      role="search"
    >
      <SearchIcon
        aria-hidden
        className={cn(
          'pointer-events-none absolute left-4 top-1/2 -translate-y-1/2 text-muted-foreground',
          compact ? 'size-4' : 'size-5',
        )}
      />
      <Input
        aria-label="Search"
        autoComplete="off"
        autoFocus={autoFocus}
        className={cn(
          'rounded-xl bg-card pl-11 pr-16 shadow-sm focus-visible:shadow-md',
          compact ? 'h-12' : 'h-16 text-base',
        )}
        maxLength={300}
        name="q"
        onChange={(event) => setValue(event.target.value)}
        placeholder="What are you looking for?"
        value={value}
      />
      <button
        type="submit"
        aria-label="Search"
        className="absolute right-2 top-1/2 flex size-11 -translate-y-1/2 items-center justify-center rounded-lg bg-primary text-primary-foreground hover:opacity-90"
      >
        <SearchIcon aria-hidden className="size-5" />
      </button>
    </form>
  );
}
