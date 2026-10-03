// The phone app's screens (V2-15 §5.2): Home, Inbox, Ask, Live and Settings, plus pairing.
import { card } from '../lib/cards.mjs';
import { pair, parsePairing } from '../lib/fabric.mjs';
import { action, h, toast, when } from './ui.mjs';

// Live ---------------------------------------------------------------------------------------------
export async function live(app) {
  const overview = await app.fabric.request('GET', '/harness/live');
  if (overview.status !== 200)
    return h(
      'section.screen',
      {},
      h('h2', { text: 'Live' }),
      h('p.empty', { text: overview.body?.detail || 'Live view is off.' }),
    );
  const targets = overview.body.targets || [];
  if (!targets.length)
    return h(
      'section.screen',
      {},
      h('h2', { text: 'Live' }),
      h('p.empty', { text: 'Nothing to watch yet.' }),
    );
  return h(
    'section.screen',
    {},
    h('h2', { text: 'Live' }),
    targets.map((t) => liveCard(app, t)),
  );
}

function liveCard(app, target) {
  const base = `/harness/live/${target.hand}/${encodeURIComponent(target.target)}`;
  const img = h('img.frame', { alt: `Live view: ${target.hand}` });
  const state = h('p.muted', { text: target.url || '' });
  const takeButton = h('button', {}, 'Take control');
  let holding = false;
  let frame = { width: 0, height: 0 };
  const refresh = async () => {
    if (!img.isConnected || document.hidden) {
      if (img.isConnected) setTimeout(refresh, 1000);
      return;
    }
    try {
      const shot = await app.fabric.request('GET', `${base}/latest.jpg`);
      if (shot.status === 200 && shot.body?.base64)
        img.src = `data:${shot.body.content_type};base64,${shot.body.base64}`;
      const control = (await app.fabric.request('GET', `${base}/control`)).body || {};
      holding = control.holder === 'human';
      takeButton.textContent = holding ? 'Give back' : 'Take control';
      img.classList.toggle('input', holding);
    } catch (error) {
      state.textContent = error.message;
    }
    if (img.isConnected) setTimeout(refresh, 1000); // At most 1 frame a second on the phone.
  };
  img.addEventListener(
    'load',
    () => (frame = { width: img.naturalWidth, height: img.naturalHeight }),
  );
  img.addEventListener('click', async (event) => {
    if (!holding || !['sandbox', 'managed'].includes(target.hand)) return;
    const box = img.getBoundingClientRect();
    const x = Math.round(((event.clientX - box.left) / box.width) * frame.width);
    const y = Math.round(((event.clientY - box.top) / box.height) * frame.height);
    try {
      const reply = await app.fabric.request('POST', `${base}/input/click`, { x, y });
      if (reply.status !== 200) toast(reply.body?.detail || 'Input was refused.', 'warn');
    } catch (error) {
      toast(error.message, 'warn');
    }
  });
  takeButton.addEventListener('click', (event) =>
    action(event.currentTarget, async () => {
      const reply = await app.fabric.request(
        'POST',
        `${base}/control/${holding ? 'release' : 'take'}`,
      );
      if (reply.status !== 200) {
        toast(reply.body?.detail || 'Control could not change.', 'warn');
        return;
      }
      toast(
        holding
          ? 'Handed back. PANINI looks again before acting.'
          : 'You have control. PANINI is waiting.',
      );
    }),
  );
  setTimeout(refresh, 0);
  return h(
    'article.card.live',
    {},
    h('h3', { text: target.hand }),
    state,
    img,
    h('div.actions', {}, takeButton),
  );
}
