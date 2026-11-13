// Interrupt cards on the phone (V2-15 §5.3): what a card offers, and the exact statement the phone signs.
// Pure functions, tested in Node (test/cards.test.mjs).

/** JSON exactly as the PC's json.dumps(sort_keys=True, separators=(",", ":")) with ensure_ascii. */
export function canonical(value) {
  if (value === null || typeof value !== 'object') {
    return JSON.stringify(value).replace(
      /[\u007f-￿]/g,
      (c) => '\\u' + c.charCodeAt(0).toString(16).padStart(4, '0'),
    );
  }
  if (Array.isArray(value)) return '[' + value.map(canonical).join(',') + ']';
  return (
    '{' +
    Object.keys(value)
      .sort()
      .map((k) => canonical(k) + ':' + canonical(value[k]))
      .join(',') +
    '}'
  );
}

export async function sha256Hex(text) {
  const digest = await globalThis.crypto.subtle.digest('SHA-256', new TextEncoder().encode(text));
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, '0')).join('');
}

/** The statement a decision is signed over: interrupt_id|nonce|action_digest|status|sha256(canonical payload). */
export async function decisionStatement(interruptId, nonce, actionDigest, status, payload) {
  return `${interruptId}|${nonce}|${actionDigest || ''}|${status}|${await sha256Hex(canonical(payload ?? {}))}`;
}

function sessionPermission(view) {
  const permission = view.preview?.mcp_permission;
  return (
    (!view.status || view.status === 'open') &&
    view.kind === 'approval' &&
    view.reason === 'mcp_effect' &&
    (view.level || 'tap') === 'tap' &&
    permission?.seconds === 3600 &&
    typeof permission.tool === 'string' &&
    permission.tool.startsWith('browser_') &&
    view.response_schema?.properties?.allow_for_session?.type === 'boolean'
  );
}

/** What the card shows and which buttons it has. */
export function card(view) {
  const level = view.level || 'tap';
  const base = {
    id: view.id,
    kind: view.kind,
    title: view.title || 'PANINI needs you',
    message: view.message || '',
    level,
  };
  if (view.status && view.status !== 'open')
    return { ...base, buttons: [], note: `Already ${view.status}.` };
  if (level === 'local') return { ...base, buttons: [], note: 'Approve this on your PC.' };
  if (level === 'verify') base.note = 'Confirm this exact decision with your passkey.';
  if (view.kind === 'approval')
    return {
      ...base,
      buttons: sessionPermission(view)
        ? ['approve', 'allow_session', 'decline']
        : ['approve', 'decline'],
      ...(sessionPermission(view)
        ? {
            note: 'Allow for 1 hour covers this tool’s navigation and input. Submissions still ask.',
          }
        : {}),
    };
  if (view.kind === 'takeover') return { ...base, buttons: ['done', 'decline'] };
  if (view.kind === 'question') return { ...base, buttons: ['answer', 'decline'], asks: true };
  return { ...base, buttons: ['decline'] };
}

/** The decision a button means (status and payload), following the interrupt's response schema. */
export function decision(view, button, answer = '') {
  if (button === 'allow_session' && sessionPermission(view))
    return { status: 'resolved', payload: { approved: true, allow_for_session: true } };
  if (button === 'approve') return { status: 'resolved', payload: { approved: true } };
  if (button === 'decline' && view.kind === 'approval')
    return { status: 'resolved', payload: { approved: false } };
  if (button === 'done') return { status: 'resolved', payload: {} };
  if (button === 'answer')
    return { status: 'resolved', payload: { answer: String(answer).slice(0, 2000) } };
  return { status: 'cancelled', payload: null };
}

/**
 * Restore before deciding: the phone fetches the interrupt again and decides only if it is still open and still
 * the same action it showed (the nonce and action digest match). Returns a reason when it must not decide.
 */
export function stale(shown, current) {
  if (!current) return 'This request is no longer available.';
  if (current.status !== 'open')
    return `Someone already ${current.status === 'resolved' ? 'answered' : current.status} this.`;
  if (
    current.nonce !== shown.nonce ||
    (current.action_digest || '') !== (shown.action_digest || '')
  )
    return 'The request changed. Review it again.';
  return '';
}
