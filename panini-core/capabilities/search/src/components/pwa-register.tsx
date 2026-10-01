import { useEffect } from 'react';

/** Register in production only: a local worker would pin stale modules over Vite's HMR. */
export function PwaRegister() {
  useEffect(() => {
    if (import.meta.env.DEV) return;
    if (!('serviceWorker' in navigator)) return;
    void navigator.serviceWorker
      .register('/search/sw.js', { scope: '/search/', updateViaCache: 'none' })
      .catch(console.error);
  }, []);
  return null;
}
