import { Link } from '@tanstack/react-router';
import { cn } from '@/lib/utils';
import { Logo } from './logo';

export function Wordmark({ size }: { size: 'sm' | 'lg' }) {
  return (
    <Link
      className={cn(
        'vt-wordmark inline-flex shrink-0 items-center font-semibold tracking-tight select-none',
        size === 'lg' ? 'gap-3 text-5xl' : 'gap-2 text-xl',
      )}
      to="/"
      viewTransition
    >
      <Logo className={size === 'lg' ? 'size-12' : 'size-9'} />
      <span className="whitespace-nowrap leading-tight">
        JevRev<span className="text-primary-text"> Search</span>
      </span>
    </Link>
  );
}
