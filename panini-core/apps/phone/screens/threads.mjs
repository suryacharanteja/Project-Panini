import { action, h, toast, when } from './ui.mjs';
import { personaChip } from './persona.mjs';
import { saveSource } from './spaces.mjs';

export async function threads(app) {
  const reply = await app.fabric.request('GET', '/threads');
  if (reply.status !== 200) throw new Error(reply.body?.detail || 'Work history is unavailable.');
  const panel = h('div');
  const show = async (id) => {
    const detail = await app.fabric.request('GET', `/threads/${encodeURIComponent(id)}`);
    if (detail.status !== 200) throw new Error(detail.body?.detail || 'This task is unavailable.');
    const task = detail.body.thread;
    const terminal = ['completed', 'failed', 'cancelled'].includes(task.status);
    panel.replaceChildren(
      h(
        'article.card',
        {},
        h('h3', { text: task.title }),
        h('p.eyebrow', { text: task.status.replaceAll('_', ' ') }),
        h('p', { text: task.goal }),
        task.result ? h('pre', { text: JSON.stringify(task.result, null, 2) }) : null,
        task.status === 'completed' && task.result?.outcome === 'verified_complete'
          ? saveSource(app, 'task', task.id)
          : null,
        ...(detail.body.messages || []).map((message) =>
          h(
            'div.persona-message',
            {},
            personaChip(message.persona),
            h('p', { text: message.text }),
          ),
        ),
        h('h3', { text: 'Activity' }),
        h(
          'ol',
          {},
          detail.body.events.map((event) =>
            h(
              'li',
              {},
              h('span', {
                text: event.detail || event.message || event.type || event.kind || 'Update',
              }),
            ),
          ),
        ),
        h(
          'div.actions',
          {},
          h(
            'button',
            { onclick: (event) => action(event.currentTarget, () => show(id)) },
            'Refresh task',
          ),
          !terminal
            ? h(
                'button.danger',
                {
                  onclick: (event) =>
                    action(event.currentTarget, async () => {
                      const stopped = await app.fabric.request(
                        'POST',
                        `/threads/${encodeURIComponent(id)}/stop`,
                      );
                      if (stopped.status !== 200)
                        throw new Error(stopped.body?.detail || 'Stop could not be confirmed.');
                      toast('Task stop recorded.');
                      await show(id);
                    }),
                },
                'Stop task',
              )
            : null,
        ),
      ),
    );
  };
  const list = reply.body.threads || [];
  return h(
    'section.screen',
    {},
    h('h2', { text: 'Work' }),
    h('button.wide', { onclick: () => app.show('conversation') }, 'Conversations · continue on PC'),
    h('p.muted', {
      text: 'Recent background tasks on your PC. Completion status and verified results are shown separately.',
    }),
    h('button.wide', { onclick: () => app.show('threads') }, 'Refresh work'),
    list.length
      ? list.map((task) =>
          h(
            'button.card.wide',
            { onclick: (event) => action(event.currentTarget, () => show(task.id)) },
            h('strong', { text: task.title }),
            h('p.muted', { text: `${task.status.replaceAll('_', ' ')} · ${when(task.created)}` }),
          ),
        )
      : h('p.empty', { text: 'No background work yet. Send an instruction from Ask.' }),
    panel,
  );
}
