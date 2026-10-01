import { cn } from '@/lib/utils';

/** The supplied Garuda eyes mark for JevRev Search. */
export function Logo({ className }: { className?: string }) {
  return (
    <img
      alt=""
      aria-hidden
      className={cn('shrink-0 rounded-md object-contain', className)}
      height={128}
      src="/search/favicon.png"
      width={128}
    />
  );
}
