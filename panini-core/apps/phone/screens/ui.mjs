// A tiny DOM helper for the phone app's screens: h('div.card', {onclick}, children). No framework.
export function h(tag, props = {}, ...children) {
  const [name, ...classes] = tag.split('.');
  const el = document.createElement(name);
  if (classes.length) el.className = classes.join(' ');
  for (const [key, value] of Object.entries(props || {})) {
    if (key.startsWith('on')) el.addEventListener(key.slice(2), value);
    else if (key === 'text') el.textContent = value;
    else if (value !== false && value != null) el.setAttribute(key, value === true ? '' : value);
  }
  for (const child of children.flat()) {
    if (child == null || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

export function toast(message, tone = '') {
  const el = h(`div.toast${tone ? '.' + tone : ''}`, { role: 'status', text: message });
  document.body.append(el);
  setTimeout(() => el.remove(), 3500);
}

export const when = (seconds) =>
  seconds
    ? new Date(seconds * 1000).toLocaleString([], { dateStyle: 'short', timeStyle: 'short' })
    : '';

/** One effect at a time, with explicit error feedback and recovery. */
export async function action(button, fn) {
  if (button.disabled) return;
  button.disabled = true;
  button.setAttribute('aria-busy', 'true');
  try {
    await fn();
  } catch (error) {
    toast(error.message || 'The action could not finish.', 'warn');
  } finally {
    button.disabled = false;
    button.removeAttribute('aria-busy');
  }
}
