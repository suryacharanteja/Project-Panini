import { action, h, toast } from './ui.mjs';

export async function conversation(app) {
  const panel = h('div');
  async function open(id) {
    const reply = await app.fabric.request('GET', `/presence/threads/${encodeURIComponent(id)}`);
    if (reply.status !== 200) throw new Error(reply.body?.detail || 'Conversation unavailable.');
    const thread = reply.body;
    panel.replaceChildren(
      h('h3', { text: thread.title || 'PANINI conversation' }),
      h('p.muted', { text: thread.summary }),
      h('pre', { text: thread.transcript }),
      h(
        'button.wide',
        {
          onclick: (event) =>
            action(event.currentTarget, async () => {
              const result = await app.fabric.request('POST', '/presence/handoff', {
                thread_id: id,
                destination: 'pc',
              });
              if (result.status !== 200)
                throw new Error(result.body?.detail || 'Handoff unavailable.');
              toast(
                'Queued for your PC. Finish any active task or voice session there to continue.',
              );
            }),
        },
        'Continue on my PC',
      ),
      h(
        'button.wide',
        {
          onclick: (event) =>
            action(event.currentTarget, async () => {
              const result = await app.fabric.request('POST', '/presence/handoff', {
                thread_id: id,
                destination: 'call',
              });
              if (result.status !== 200)
                throw new Error(
                  result.body?.detail ||
                    result.body?.error?.message ||
                    'Owner call review unavailable.',
                );
              toast(
                'Review the fixed owner-call brief in Inbox. No call starts until you approve it.',
              );
              await app.show('inbox');
            }),
        },
        'Review a call to me',
      ),
    );
  }
  const reply = await app.fabric.request('GET', '/presence/threads');
  if (reply.status !== 200) throw new Error(reply.body?.detail || 'Conversations unavailable.');
  if (app.threadTarget) {
    const target = app.threadTarget;
    app.threadTarget = '';
    await open(target);
  }
  return h(
    'section.screen',
    {},
    h('h2', { text: 'Conversations' }),
    h('p.muted', {
      text: 'Your original PANINI threads. Continue on the PC without starting a new conversation.',
    }),
    ...(reply.body.threads || []).map((thread) =>
      h(
        'button.card.wide',
        {
          onclick: (event) => action(event.currentTarget, () => open(thread.id)),
        },
        thread.title || 'PANINI conversation',
      ),
    ),
    panel,
    h('button.wide', { onclick: () => app.show('threads') }, 'Back to Work'),
  );
}
