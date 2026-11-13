// Owner recording boundary. Playwright's event sink receives only filtered code.
// Sensitive controls are blocked while recording; Stop recording before entering credentials.
'use strict';

const fs = require('node:fs/promises');
const path = require('node:path');
const { randomUUID } = require('node:crypto');

const sensitive =
  /password|passwd|passcode|one.time|\bpin\b|\b(?:cvv|cvc|otp)\b|credit.card|card.number|security.code|verification.code/i;
const secret =
  /sk-[a-z0-9_-]{12,}|api[_-]?key|password|passwd|secret|token\s*[:=]|-----BEGIN|\b\d{13,19}\b/i;
const manual = 'panini_manual()'; // Parser turns this into a generic manual step, never the raw source.

function install() {
  window.__paniniTeachGuard = true;
  const blocked = (element) => {
    if (!(element instanceof Element)) return true;
    const autocomplete = element.getAttribute('autocomplete') || '';
    return (
      element.getAttribute('type') === 'password' ||
      /password|one-time-code|cc-/i.test(autocomplete) ||
      /password|passwd|passcode|one.time|\bpin\b|\b(?:cvv|cvc|otp)\b|credit.card|card.number|security.code|verification.code/i.test(
        [
          element.id,
          element.getAttribute('name'),
          element.getAttribute('aria-label'),
          element.getAttribute('placeholder'),
          ...Array.from(element.labels || [], (label) => label.textContent),
        ].join(' '),
      )
    );
  };
  const mount = () => {
    if (!document.documentElement || document.getElementById('panini-recording-badge')) return;
    const badge = document.createElement('div');
    badge.id = 'panini-recording-badge';
    badge.textContent = '● Recording · Stop in PANINI Skills';
    badge.style.cssText =
      'display:none;position:fixed;right:10px;top:10px;z-index:2147483647;font:600 12px system-ui;background:#8e2228;color:white;border-radius:20px;padding:9px 14px;pointer-events:none';
    document.documentElement.appendChild(badge);
    setInterval(() => {
      badge.style.display = window.__paniniTeachRecording ? 'block' : 'none';
    }, 250);
  };
  document.addEventListener('DOMContentLoaded', mount, { once: true });
  mount();
  const guard = (event) => {
    if (window.__paniniTeachRecording && blocked(event.target)) {
      event.preventDefault();
      event.stopImmediatePropagation();
    }
  };
  for (const type of ['keydown', 'beforeinput', 'paste', 'drop', 'compositionstart'])
    document.addEventListener(type, guard, true);
}

async function init({ page }) {
  const context = page.context();
  page.on('framenavigated', (frame) =>
    frame
      .evaluate((active) => {
        window.__paniniTeachRecording = active;
      }, !!context.__paniniTeachOn)
      .catch(() => {}),
  );
  if (context.__paniniTeachInstalled) return;
  context.__paniniTeachInstalled = true;
  await context.addInitScript(install);
  for (const existing of context.pages()) await existing.evaluate(install).catch(() => {});
  const enable = context._enableRecorder.bind(context);
  const disable = context._disableRecorder.bind(context);
  let pending = Promise.resolve();
  let folder = null,
    index = 0;
  async function capture(current, label) {
    if (!folder) return;
    try {
      let safe = true;
      for (const frame of current.frames()) {
        if (
          !(await frame.evaluate(
            () =>
              ![...document.querySelectorAll('input,textarea,[contenteditable=true]')].some(
                (e) =>
                  e.getAttribute('type') === 'password' ||
                  /password|one-time-code|cc-/i.test(e.getAttribute('autocomplete') || '') ||
                  /password|passcode|one.time|\bpin\b|\b(?:cvv|cvc|otp)\b|credit.card|card.number|security.code/i.test(
                    [
                      e.id,
                      e.getAttribute('name'),
                      e.getAttribute('aria-label'),
                      ...Array.from(e.labels || [], (l) => l.textContent),
                    ].join(' '),
                  ) ||
                  /sk-[a-z0-9_-]{12,}|api[_-]?key|password|passwd|secret|token\s*[:=]|-----BEGIN|\b\d{13,19}\b/i.test(
                    e.value || e.textContent || '',
                  ),
              ),
          ))
        )
          safe = false;
      }
      if (!safe) return;
      const snapshot = await current.ariaSnapshot({ timeout: 2000 });
      if (snapshot.length > 60000 || secret.test(snapshot)) return;
      await fs.writeFile(path.join(folder, `${label}.txt`), snapshot, {
        encoding: 'utf8',
        flag: 'w',
      });
      if (label.startsWith('step-')) {
        const image = await current.screenshot({ type: 'png', timeout: 2000 });
        if (image.length <= 2000000) await fs.writeFile(path.join(folder, `${label}.png`), image);
      }
    } catch {
      /* Missing evidence is retained as missing, never invented. */
    }
  }
  async function toggle(on) {
    context.__paniniTeachOn = on;
    for (const current of context.pages()) {
      for (const frame of current.frames())
        await frame
          .evaluate((active) => {
            window.__paniniTeachRecording = active;
          }, on)
          .catch(() => {});
    }
  }
  context.on('page', (current) =>
    current.on('domcontentloaded', () => {
      current
        .evaluate((active) => {
          window.__paniniTeachRecording = active;
        }, !!context.__paniniTeachOn)
        .catch(() => {});
    }),
  );
  // Navigation and newly attached frames inherit the notice and sensitive-control guard.
  page.on('framenavigated', (frame) =>
    frame
      .evaluate((active) => {
        window.__paniniTeachRecording = active;
      }, !!context.__paniniTeachOn)
      .catch(() => {}),
  );
  context._enableRecorder = async (params, sink) => {
    const wrapped = {};
    index = 0;
    folder = process.env.PANINI_TEACH_EVIDENCE_ROOT
      ? path.join(process.env.PANINI_TEACH_EVIDENCE_ROOT, randomUUID())
      : null;
    if (folder) await fs.mkdir(folder, { recursive: true });
    for (const event of ['actionAdded', 'actionUpdated', 'signalAdded']) {
      wrapped[event] = (current, data, code) => {
        pending = pending
          .then(async () => {
            let safe = typeof code === 'string' && code.length <= 12000 && !secret.test(code);
            const action = data.action || data;
            if (sensitive.test(String(action.selector || ''))) safe = false;
            if (action.name === 'fill' || action.name === 'press' || action.name === 'select') {
              try {
                const info = await current.locator(action.selector).evaluate(
                  (element) => ({
                    type: element.getAttribute('type'),
                    autocomplete: element.getAttribute('autocomplete'),
                    label: [
                      element.id,
                      element.getAttribute('name'),
                      element.getAttribute('aria-label'),
                      element.getAttribute('placeholder'),
                      ...Array.from(element.labels || [], (l) => l.textContent),
                    ].join(' '),
                  }),
                  undefined,
                  { timeout: 2000 },
                );
                if (
                  info.type === 'password' ||
                  /password|one-time-code|cc-/i.test(info.autocomplete || '') ||
                  sensitive.test(info.label)
                )
                  safe = false;
              } catch {
                safe = false;
              }
            }
            sink[event]?.(current, data, safe ? code : manual);
            if (event === 'actionAdded') index += 1;
            if (safe && index > 0 && index <= 100)
              await capture(current, `step-${String(index).padStart(3, '0')}`);
          })
          .catch(() => {});
      };
    }
    await toggle(true);
    await capture(page, 'start');
    try {
      return await enable({ ...params, language: 'python' }, wrapped);
    } catch (error) {
      await toggle(false);
      throw error;
    }
  };
  context._disableRecorder = async () => {
    try {
      await disable();
      await pending;
      await capture(page, 'end');
    } finally {
      await toggle(false);
      folder = null;
    }
  };
}

module.exports = { default: init };
