// PANINI managed browser badge (V2-08). Runs in every page before its own scripts (--init-script), so the
// person can always tell PANINI's browser apart from their own. Purely visual: it never reads the page.
(() => {
  if (window.top !== window || window.__paniniBadge) return;
  window.__paniniBadge = true;
  const mount = () => {
    if (!document.documentElement || document.getElementById('panini-managed-badge')) return;
    const badge = document.createElement('div');
    badge.id = 'panini-managed-badge';
    badge.textContent = 'PANINI browser';
    badge.setAttribute('aria-hidden', 'true');
    badge.style.cssText = [
      'position:fixed',
      'right:10px',
      'bottom:10px',
      'z-index:2147483647',
      'pointer-events:none',
      'font:600 11px/1.6 system-ui,sans-serif',
      'color:#111',
      'background:#c98a4b',
      'border-radius:999px',
      'padding:2px 10px',
      'opacity:.85',
      'box-shadow:0 1px 4px rgba(0,0,0,.3)',
    ].join(';');
    document.documentElement.appendChild(badge);
  };
  if (document.readyState === 'loading')
    document.addEventListener('DOMContentLoaded', mount, { once: true });
  else mount();
})();
