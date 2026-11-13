import * as db from '../lib/db.mjs';
// The phone app's screens (V2-15 §5.2): Home, Inbox, Ask, Live and Settings, plus pairing.
import { card } from '../lib/cards.mjs';
import { pair, parsePairing } from '../lib/fabric.mjs';
import { action, h, toast, when } from './ui.mjs';
import { addressedRequest } from '../lib/persona.mjs';

// Ask ----------------------------------------------------------------------------------------------
export async function ask(app, shared) {
  const metadata = !shared ? await app.fabric.request('GET', '/personas') : null;
  const personas = metadata?.status === 200 && metadata.body.enabled ? metadata.body.personas : [];
  const chooser = personas.length
    ? h(
        'select',
        { 'aria-label': 'Work with' },
        personas.map((p) => h('option', { value: p.id, text: p.name })),
      )
    : null;
  if (chooser) chooser.value = metadata.body.default_id;
  const box = h('textarea', {
    'aria-label': 'Ask PANINI',
    rows: 5,
    placeholder: 'Ask PANINI to do something…',
    maxlength: shared ? 1000 : 4000,
  });
  box.value = shared ? '' : (await db.get('ask-draft')) || '';
  box.addEventListener('input', () => {
    if (!shared)
      void db.set('ask-draft', box.value).catch(() => toast('Could not save your draft.', 'warn'));
  });
  const attached = [];
  const files = h('ul.attachments');
  const showFiles = () =>
    files.replaceChildren(
      ...attached.map((file, index) =>
        h(
          'li',
          {},
          `${file.name} (${Math.ceil(file.bytes / 1024)} KB) `,
          h(
            'button',
            {
              'aria-label': `Remove ${file.name}`,
              onclick: () => {
                attached.splice(index, 1);
                showFiles();
              },
            },
            'Remove',
          ),
        ),
      ),
    );
  const picker = h('input', {
    type: 'file',
    accept: 'image/jpeg,image/png,image/webp,application/pdf,text/plain',
    hidden: true,
  });
  picker.addEventListener('change', () =>
    action(attach, async () => {
      for (const file of picker.files || []) attached.push(await upload(app, file));
      picker.value = '';
      showFiles();
    }),
  );
  const attach = h(
    'button',
    { onclick: () => picker.click(), disabled: Boolean(shared) },
    'Attach photo or file',
  );
  const mic = h(
    'button',
    {
      'aria-label': 'Dictate',
      onclick: (event) => action(event.currentTarget, () => dictate(app, box, mic)),
    },
    'Dictate',
  );
  const send = async () => {
    const text = box.value.trim();
    if (!text && !shared) return;
    const request = chooser ? addressedRequest(text, chooser.value) : text;
    if (request.length > 4000)
      throw new Error('Shorten your request to leave room for the specialist name.');
    const reply = shared
      ? await app.fabric.request('POST', '/share', { ...shared, note: text })
      : await app.fabric.request('POST', '/ask', {
          text: request,
          attachments: attached.map((file) => file.id),
          ...(app.thread ? { thread_id: app.thread.id } : {}),
        });
    if (reply.status === 200) {
      toast(shared ? 'Sent to PANINI.' : 'PANINI is on it. Approvals will come to your Inbox.');
      if (!shared) await db.remove('ask-draft');
      app.thread = null;
      app.shared = null;
      app.show('home');
    } else toast(reply.body?.detail || 'Could not send.', 'warn');
  };
  return h(
    'section.screen',
    {},
    h('h2', { text: shared ? 'Shared with PANINI' : 'Ask' }),
    shared
      ? h(
          'div.card.shared',
          {},
          h('strong', { text: shared.title || shared.url || 'Shared item' }),
          h('p.muted', { text: (shared.text || shared.url || '').slice(0, 400) }),
        )
      : null,
    shared ? h('p.muted', { text: 'What should PANINI do with it? (Optional.)' }) : null,
    app.thread && !shared
      ? h(
          'div.card.continuing',
          {},
          h('p.eyebrow', { text: 'Continuing' }),
          h('strong', { text: app.thread.title }),
          h(
            'button',
            {
              onclick: () => {
                app.thread = null;
                app.show('ask');
              },
            },
            'Start a new conversation instead',
          ),
        )
      : null,
    chooser ? h('label', {}, 'Work with', chooser) : null,
    box,
    shared ? null : h('div.actions', {}, attach, mic),
    picker,
    files,
    h('button.primary.wide', { onclick: (event) => action(event.currentTarget, send) }, 'Send'),
  );
}

const UPLOAD_MAX = 700 * 1024;

/** Send a photo or file to the PC over the fabric; the PC keeps it as an untrusted artifact. */
async function upload(app, file) {
  if (file.size > UPLOAD_MAX) throw new Error(`${file.name} is larger than 700 KB.`);
  const bytes = new Uint8Array(await file.arrayBuffer());
  let binary = '';
  for (let i = 0; i < bytes.length; i += 0x8000)
    binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  const reply = await app.fabric.request('POST', '/attachments', {
    name: file.name.slice(0, 120) || 'file',
    mime: file.type || 'application/octet-stream',
    base64: btoa(binary),
  });
  if (reply.status !== 200) throw new Error(reply.body?.detail || 'The file could not be sent.');
  return reply.body;
}

/** Dictation: Android Chrome's speech recognition when available, otherwise record and transcribe on the PC. */
async function dictate(app, box, button) {
  const Recognition = globalThis.SpeechRecognition || globalThis.webkitSpeechRecognition;
  const append = (text) => {
    box.value = [box.value.trim(), text.trim()].filter(Boolean).join(' ');
    box.dispatchEvent(new Event('input'));
  };
  if (Recognition) {
    const recognition = new Recognition();
    recognition.lang = navigator.language || 'en-US';
    recognition.interimResults = false;
    button.textContent = 'Listening…';
    try {
      const text = await new Promise((resolve, reject) => {
        recognition.onresult = (event) => resolve(event.results[0][0].transcript);
        recognition.onerror = (event) =>
          reject(
            new Error(
              event.error === 'not-allowed'
                ? 'Microphone access is blocked.'
                : 'Speech was not recognised.',
            ),
          );
        recognition.onend = () => resolve('');
        recognition.start();
      });
      if (text) append(text);
    } finally {
      button.textContent = 'Dictate';
    }
    return;
  }
  if (!navigator.mediaDevices?.getUserMedia || !globalThis.MediaRecorder)
    throw new Error('Dictation is not available here.');
  const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  const recorder = new MediaRecorder(stream);
  const chunks = [];
  recorder.ondataavailable = (event) => chunks.push(event.data);
  const stopped = new Promise((resolve) => (recorder.onstop = resolve));
  recorder.start();
  button.textContent = 'Recording… (5 s)';
  await new Promise((resolve) => setTimeout(resolve, 5000));
  recorder.stop();
  await stopped;
  stream.getTracks().forEach((track) => track.stop());
  button.textContent = 'Dictate';
  const blob = new Blob(chunks, { type: recorder.mimeType || 'audio/webm' });
  if (blob.size > 700 * 1024) throw new Error('That recording is too long. Try a shorter one.');
  const bytes = new Uint8Array(await blob.arrayBuffer());
  let binary = '';
  for (let i = 0; i < bytes.length; i += 0x8000)
    binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  const reply = await app.fabric.request('POST', '/dictation', {
    mime: blob.type,
    base64: btoa(binary),
  });
  if (reply.status !== 200)
    throw new Error(reply.body?.detail || 'The PC could not transcribe that.');
  if (reply.body.text) append(reply.body.text);
}
