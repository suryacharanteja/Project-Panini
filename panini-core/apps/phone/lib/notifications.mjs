// Resolved ids are unique approval ids. Retain a bounded tombstone beyond the push TTL, so a delayed
// opening push cannot resurrect a decision already made on another surface.
export function rememberClosed(previous, id, now = Date.now()) {
  const entries = Object.entries(previous || {}).filter(([, at]) => now - at < 86400000);
  entries.push([id, now]);
  return Object.fromEntries(entries.slice(-1000));
}

export function notificationPath(data) {
  if (data.type === 'interrupt') return '/?tab=inbox';
  if (typeof data.thread_id === 'string' && /^[A-Za-z0-9_-]{1,200}$/.test(data.thread_id))
    return `/?tab=conversation&thread=${encodeURIComponent(data.thread_id)}`;
  return '/?tab=inbox';
}
