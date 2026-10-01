/* Jev-Rev content script — one atomic snapshot of what can be done on this page.
 *
 * Shaped after browser-use's jev-ultrafast snapshot (MIT): an indexed action
 * space rather than a DOM dump, with accessible names, current values, and a
 * freshness marker so a decision made about an old page is never executed.
 */
(() => {
  const CONTENT_VERSION = '3.2.0';
  // Re-injection after an extension reload must replace, not skip, the handler.
  if (window.__jevRevListener) {
    try {
      chrome.runtime.onMessage.removeListener(window.__jevRevListener);
    } catch {}
  }
  const cache = (window.__jevRev ||= { ids: new WeakMap(), nodes: new Map(), next: 1 });

  const identity = (el) => {
    if (!cache.ids.has(el)) cache.ids.set(el, cache.next++);
    const id = cache.ids.get(el);
    cache.nodes.set(id, el);
    return id;
  };

  // Never touch a password, file or hidden field.
  const safe = (el) => !['password', 'file', 'hidden'].includes(el.type);

  const visible = (el) =>
    !el.closest('[aria-hidden="true"],[inert]') &&
    el.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true });

  /** Accessible name, close enough to the spec for real pages. */
  function name(el, seen = new Set()) {
    if (!el || seen.has(el)) return '';
    seen.add(el);
    const referenced = (el.getAttribute?.('aria-labelledby') || '')
      .split(/\s+/)
      .map((id) =>
        name(el.getRootNode?.().getElementById?.(id) || document.getElementById(id), seen),
      )
      .filter(Boolean)
      .join(' ');
    return (
      referenced ||
      el.getAttribute?.('aria-label') ||
      [...(el.labels || [])]
        .map((l) => name(l, seen))
        .filter(Boolean)
        .join(' ') ||
      (['button', 'submit', 'reset'].includes(el.type) ? el.value : '') ||
      el.getAttribute?.('alt') ||
      (el.tagName === 'INPUT'
        ? ''
        : [...el.childNodes]
            .map((n) =>
              n.nodeType === 3
                ? n.textContent
                : n.nodeType === 1 && n.getAttribute('aria-hidden') !== 'true'
                  ? name(n, seen)
                  : '',
            )
            .join(' ')
            .trim()) ||
      el.getAttribute?.('title') ||
      el.getAttribute?.('placeholder') ||
      el.getAttribute?.('data-control-name') ||
      el.getAttribute?.('data-icon-name') ||
      el.getAttribute?.('data-test-icon') ||
      el.querySelector?.('svg[data-test-icon]')?.getAttribute('data-test-icon') ||
      el.querySelector?.('svg[aria-label]')?.getAttribute('aria-label') ||
      (
        el.querySelector?.('svg use')?.getAttribute('href') ||
        el.querySelector?.('svg use')?.getAttribute('xlink:href') ||
        ''
      )
        .replace(/^#/, '')
        .replace(/[-_]/g, ' ') ||
      ''
    )
      .replace(/\s+/g, ' ')
      .trim()
      .slice(0, 90);
  }

  const ROLES = [
    'button',
    'link',
    'checkbox',
    'radio',
    'switch',
    'tab',
    'menuitem',
    'menuitemradio',
    'option',
    'combobox',
    'textbox',
    'searchbox',
    'spinbutton',
  ];
  const SELECTOR =
    'a[href],button,input,textarea,select,summary,[contenteditable="true"],[tabindex],[onclick],[aria-haspopup],' +
    ROLES.map((r) => `[role="${r}"]`).join(',');

  function roots() {
    const found = [document];
    for (const root of found)
      for (const el of root.querySelectorAll('*')) if (el.shadowRoot) found.push(el.shadowRoot);
    return found;
  }
  const queryAll = (selector) => roots().flatMap((root) => [...root.querySelectorAll(selector)]);

  function role(el) {
    const explicit = el.getAttribute('role');
    if (ROLES.includes(explicit)) return explicit;
    if (el.tagName === 'BUTTON' || el.tagName === 'SUMMARY') return 'button';
    if (el.tagName === 'A') return 'link';
    if (el.tagName === 'SELECT') return 'combobox';
    if (el.tagName === 'TEXTAREA' || el.isContentEditable) return 'textbox';
    if (el.tagName === 'INPUT') {
      if (['checkbox', 'radio'].includes(el.type)) return el.type;
      if (['button', 'submit', 'reset', 'image'].includes(el.type)) return 'button';
      if (el.type === 'search') return 'searchbox';
      if (el.type === 'number') return 'spinbutton';
      if (['text', 'email', 'url', 'tel'].includes(el.type)) return 'textbox';
    }
    if (el.tabIndex >= 0 || el.hasAttribute('onclick') || el.hasAttribute('aria-haspopup'))
      return 'button';
    if (['DIV', 'SPAN', 'svg'].includes(el.tagName) && getComputedStyle(el).cursor === 'pointer')
      return 'button';
    return null;
  }

  function controls() {
    const candidates = new Set(queryAll(SELECTOR));
    // Some sites implement icon-only search as a clickable div/SVG with no
    // semantic role. Include the outer pointer target, not all its descendants.
    for (const el of queryAll('div,span,svg')) {
      if (el.closest('#__jevRev_cursor') || !visible(el) || candidates.has(el)) continue;
      if (getComputedStyle(el).cursor !== 'pointer') continue;
      if (
        el.closest(SELECTOR) ||
        (el.parentElement && getComputedStyle(el.parentElement).cursor === 'pointer')
      )
        continue;
      if (el.querySelector('a[href],button,input,textarea,select,[role="button"],[role="link"]'))
        continue;
      candidates.add(el);
    }
    for (const icon of queryAll('svg[data-test-icon],svg[aria-label]')) {
      if (
        !icon.closest(SELECTOR) &&
        visible(icon) &&
        ![...candidates].some((el) => el !== icon && el.contains(icon))
      )
        candidates.add(icon);
    }
    return [...candidates].sort((a, b) =>
      a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1,
    );
  }

  const marker = () => cache.marker || 'ready';

  // Guard the selected control and its form values. Unrelated autocomplete
  // suggestions or page widgets must not invalidate every pending action.
  const signature = (el) =>
    JSON.stringify([
      location.href,
      name(el),
      role(el),
      el.type,
      el.value,
      el.checked,
      el.isContentEditable ? el.textContent : '',
      el.getAttribute('href'),
      el.disabled,
      el.readOnly,
      el.getAttribute('aria-disabled'),
      el.getAttribute('aria-readonly'),
      el.form
        ? [...el.form.querySelectorAll('input,textarea,select')]
            .filter(
              (field) =>
                safe(field) && !['submit', 'button', 'reset', 'image'].includes(field.type),
            )
            .map((field) => [identity(field), field.value, field.checked, field.disabled])
        : [],
    ]);

  function blockedReason() {
    const googleChallenge =
      /(^|\.)google\.[a-z.]+$/.test(location.hostname) && location.pathname.startsWith('/sorry/');
    const challengeFrame = [...document.querySelectorAll('iframe')].some(
      (frame) =>
        /recaptcha|hcaptcha|challenge/i.test(frame.src + ' ' + frame.title) &&
        visible(frame) &&
        frame.getBoundingClientRect().height > 50 &&
        frame.getBoundingClientRect().width > 200,
    );
    return googleChallenge || challengeFrame
      ? 'This page requires human verification. Complete it in the attached tab, then run your instruction again.'
      : '';
  }

  function visibleText(limit = 4000) {
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    const range = document.createRange();
    const out = [];
    let node,
      length = 0;
    while ((node = walker.nextNode()) && length < limit) {
      const value = node.textContent.trim();
      const parent = node.parentElement;
      if (
        !value ||
        !parent ||
        parent.closest('script,style,noscript,template,#__jevRev_cursor') ||
        !visible(parent)
      )
        continue;
      range.selectNodeContents(node);
      const r = range.getBoundingClientRect();
      if (
        r.width > 0 &&
        r.height > 0 &&
        r.bottom > 0 &&
        r.top < innerHeight &&
        r.right > 0 &&
        r.left < innerWidth
      ) {
        out.push(value);
        length += value.length;
      }
    }
    return out.join('\n').slice(0, limit);
  }

  /** Everything that can be done right now, each with a stable id. */
  function snapshot(limit = 120) {
    cache.nodes.clear();
    const actions = [];
    for (const el of controls()) {
      if (
        !safe(el) ||
        !visible(el) ||
        el.matches(':disabled') ||
        el.closest('[aria-disabled="true"]')
      )
        continue;
      const r = el.getBoundingClientRect();
      const x = r.x + r.width / 2,
        y = r.y + r.height / 2;
      const rname = role(el) || (el.tagName === 'svg' ? 'button' : null);
      if (
        !rname ||
        r.width <= 0 ||
        r.height <= 0 ||
        x < 0 ||
        y < 0 ||
        x >= innerWidth ||
        y >= innerHeight
      )
        continue;

      const base = { node: identity(el), role: rname, label: name(el) || rname };
      base.context = [el.tagName, el.id, el.getAttribute('class')]
        .filter(Boolean)
        .join(' ')
        .slice(0, 240);
      base.bounds = {
        x: Math.round(r.x),
        y: Math.round(r.y),
        width: Math.round(r.width),
        height: Math.round(r.height),
      };
      if (el.tagName === 'A') base.href = el.href;
      for (const key of ['checked', 'selected', 'expanded']) {
        const v = el.getAttribute('aria-' + key);
        if (v !== null) base[key] = v;
      }
      if (['checkbox', 'radio'].includes(el.type)) base.checked = String(el.checked);

      if (el.tagName === 'SELECT') {
        for (const o of el.options) {
          if (o.selected || o.disabled) continue;
          actions.push({
            ...base,
            kind: 'select',
            value: o.value,
            current_value: [...el.selectedOptions].map((s) => s.label).join(', '),
            label: base.label + ' → ' + o.label,
          });
        }
      } else {
        const editable =
          !el.readOnly &&
          el.getAttribute('aria-readonly') !== 'true' &&
          (el.isContentEditable ||
            ['textbox', 'searchbox', 'spinbutton'].includes(rname) ||
            (rname === 'combobox' && ['INPUT', 'TEXTAREA'].includes(el.tagName)));
        const value =
          'value' in el
            ? String(el.value)
            : el.isContentEditable || rname === 'combobox'
              ? el.innerText.trim()
              : '';
        actions.push({ ...base, kind: editable ? 'fill' : 'click', value });
        if (editable && el.form && value)
          actions.push({ ...base, kind: 'submit', value, label: base.label });
        // SPA search controls often listen for Enter without owning a form.
        if (editable && !el.form && value && ['INPUT', 'TEXTAREA'].includes(el.tagName))
          actions.push({ ...base, kind: 'press_enter', value, label: base.label });
      }
      if (actions.length >= limit) break;
    }

    actions.forEach((a, i) => {
      a.id = `e${a.node}_${a.kind}${a.kind === 'select' ? '_' + i : ''}`;
    });
    if (scrollY + innerHeight < document.documentElement.scrollHeight - 2)
      actions.push({ id: 'scroll_down', kind: 'scroll', label: 'Scroll down', delta: 560 });
    if (scrollY > 0)
      actions.push({ id: 'scroll_up', kind: 'scroll', label: 'Scroll up', delta: -560 });
    // Media on the page (YouTube and any other video or audio): pause and play directly.
    const media = [...document.querySelectorAll('video, audio')].filter((m) => m.readyState > 0);
    if (skipAdButton())
      actions.push({
        id: 'media_skip_ad',
        kind: 'media',
        label: 'Skip the ad (Skip button is showing)',
      });
    if (media.some((m) => !m.paused))
      actions.push({ id: 'media_pause', kind: 'media', label: 'Pause the playing video or audio' });
    if (media.some((m) => m.paused))
      actions.push({ id: 'media_play', kind: 'media', label: 'Play the paused video or audio' });
    actions.push({ id: 'wait', kind: 'wait', label: 'Wait for the page to update' });

    cache.actions = new Map(actions.filter((a) => a.node).map((a) => [a.id, a.node]));
    cache.observations = new Map(
      actions.filter((a) => a.node).map((a) => [a.id, signature(cache.nodes.get(a.node))]),
    );
    cache.kinds = new Map(actions.map((a) => [a.id, a.kind]));
    cache.marker = crypto.randomUUID();
    cache.url = location.href;
    return {
      url: location.href,
      title: document.title,
      text: visibleText(),
      viewport: { x: Math.round(scrollX), y: Math.round(scrollY), height: innerHeight },
      actions,
      marker: marker(),
      blocked_reason: blockedReason(),
      content_version: CONTENT_VERSION,
      media: mediaState(),
    };
  }

  // The page's real media state, so PANINI reports what is on screen, never what it hoped.
  const AD_SKIP =
    '.ytp-skip-ad-button, .ytp-ad-skip-button, .ytp-ad-skip-button-modern, .ytp-ad-skip-button-slot button';
  function skipAdButton() {
    const visible = (b) => {
      const r = b.getBoundingClientRect();
      return r.width > 0 && r.height > 0 && getComputedStyle(b).visibility !== 'hidden';
    };
    // Known classes first; YouTube renames them, so also any visible "Skip" button in the player.
    const known = [...document.querySelectorAll(AD_SKIP)].find(visible);
    if (known) return known;
    const player = document.querySelector('#movie_player, .html5-video-player');
    return (
      [...(player || document).querySelectorAll('button, [role="button"]')].find(
        (b) =>
          visible(b) &&
          /^\s*skip(\s+ads?)?\s*$/i.test(b.innerText || b.getAttribute('aria-label') || ''),
      ) || null
    );
  }
  function mediaState() {
    const media = [...document.querySelectorAll('video, audio')].filter((m) => m.readyState > 0);
    if (!media.length) return null;
    const player = document.querySelector('#movie_player, .html5-video-player');
    return {
      playing: media.some((m) => !m.paused),
      ad_showing: Boolean(
        player?.classList.contains('ad-showing') ||
        document.querySelector('.ytp-ad-player-overlay'),
      ),
      ad_skippable_now: Boolean(skipAdButton()),
    };
  }

  function node(id) {
    const el = cache.nodes.get(cache.actions?.get(id));
    if (!el || !el.isConnected) throw new Error(`${id} is gone — take a fresh snapshot.`);
    return el;
  }

  /** Refuse to click something that has moved under another element. */
  function hitTest(el) {
    const r = el.getBoundingClientRect();
    let top = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
    while (top?.shadowRoot) {
      const inner = top.shadowRoot.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
      if (!inner || inner === top) break;
      top = inner;
    }
    if (top && top !== el && !el.contains(top) && !top.contains(el))
      throw new Error('that control is covered by something else — take a fresh snapshot.');
  }

  /* ---- the agent's cursor, drawn in the page ----
   * Same idea as the desktop overlay: you should be able to watch what it does.
   * Everything lives in one fixed-position layer that ignores pointer events, so
   * it can never intercept a click meant for the page.
   */
  const UI = (() => {
    let root, dot, ring, tag;
    const make = () => {
      if (root) return;
      root = document.createElement('div');
      root.id = '__jevRev_cursor';
      root.style.cssText = 'position:fixed;inset:0;z-index:2147483647;pointer-events:none;';
      root.innerHTML = `
      <div data-ring style="position:absolute;width:64px;height:64px;margin:-32px 0 0 -32px;
        border:3px solid #3b5bdb;border-radius:50%;box-shadow:0 0 0 3px rgba(138,164,255,.35),0 0 26px rgba(59,91,219,.75);
        transition:left .45s cubic-bezier(.22,.9,.28,1),top .45s cubic-bezier(.22,.9,.28,1),
        transform .18s ease,border-color .18s ease;opacity:0;"></div>
      <div data-dot style="position:absolute;width:12px;height:12px;margin:-6px 0 0 -6px;background:#3b5bdb;
        border:2px solid #fff;border-radius:50%;box-shadow:0 2px 8px rgba(0,0,0,.5);
        transition:left .45s cubic-bezier(.22,.9,.28,1),top .45s cubic-bezier(.22,.9,.28,1);opacity:0;"></div>
      <div data-tag style="position:absolute;transform:translate(22px,-34px);background:#12121a;color:#eef;
        font:600 12px/1.5 system-ui,sans-serif;padding:4px 9px;border:2px solid #3b5bdb;border-radius:7px;
        white-space:nowrap;opacity:0;transition:opacity .2s;max-width:340px;overflow:hidden;
        text-overflow:ellipsis;"></div>`;
      (document.body || document.documentElement).appendChild(root);
      [ring, dot, tag] = ['[data-ring]', '[data-dot]', '[data-tag]'].map((q) =>
        root.querySelector(q),
      );
    };

    const at = (el) => {
      const r = el.getBoundingClientRect();
      return { x: r.x + r.width / 2, y: r.y + r.height / 2, r };
    };

    return {
      /** Glide to an element and name what is about to happen. */
      async point(el, label) {
        make();
        const { x, y, r } = at(el);
        for (const node of [ring, dot, tag]) {
          node.style.left = x + 'px';
          node.style.top = y + 'px';
          node.style.opacity = '1';
        }
        ring.style.width = Math.max(48, Math.min(r.width + 26, 260)) + 'px';
        ring.style.height = Math.max(48, Math.min(r.height + 26, 160)) + 'px';
        ring.style.margin = `-${parseFloat(ring.style.height) / 2}px 0 0 -${parseFloat(ring.style.width) / 2}px`;
        ring.style.borderRadius = r.height > 60 ? '14px' : '999px';
        tag.textContent = label || '';
        await new Promise((r2) => setTimeout(r2, 480)); // let the glide finish
      },
      /** Flash green at the moment of the click. */
      async hit() {
        if (!ring) return;
        ring.style.borderColor = '#4cc27f';
        ring.style.transform = 'scale(.82)';
        await new Promise((r) => setTimeout(r, 170));
        ring.style.transform = 'scale(1)';
        ring.style.borderColor = '#3b5bdb';
      },
      hide() {
        for (const n of [ring, dot, tag]) if (n) n.style.opacity = '0';
      },
    };
  })();

  const listener = (msg, _sender, reply) => {
    try {
      if (msg.kind === 'snapshot') {
        reply(snapshot(msg.limit ?? 120));
      } else if (msg.kind === 'marker') {
        reply({ marker: marker(), version: CONTENT_VERSION });
      } else if (msg.kind === 'act') {
        if (!msg.marker || msg.marker !== marker() || cache.url !== location.href)
          throw new Error('the page changed — take a fresh snapshot.');
        if (msg.id === 'scroll_down' || msg.id === 'scroll_up') {
          // Smooth animation may never advance in a background tab. Finish the
          // scroll before acknowledging it so the next snapshot sees its result.
          scrollBy({ top: msg.id === 'scroll_down' ? 560 : -560, behavior: 'instant' });
          reply({ ok: 'scrolled', scrollY: Math.round(scrollY) });
          return true;
        }
        if (msg.id === 'wait') {
          reply({ ok: 'waited' });
          return true;
        }
        if (msg.id === 'media_skip_ad') {
          const button = skipAdButton();
          if (!button) {
            reply({
              error: 'No Skip button is showing: the ad is not skippable yet (or at all).',
              media: mediaState(),
            });
            return true;
          }
          button.click();
          // Report what the page shows afterwards, not what was intended.
          setTimeout(() => reply({ ok: 'clicked Skip', media: mediaState() }), 700);
          return true;
        }
        if (msg.id === 'media_pause' || msg.id === 'media_play') {
          const items = [...document.querySelectorAll('video, audio')].filter(
            (m) => m.readyState > 0,
          );
          if (!items.length) throw new Error('There is no video or audio on this page.');
          for (const m of items) {
            if (msg.id === 'media_pause') m.pause();
            else void m.play().catch(() => {});
          }
          reply({ ok: msg.id === 'media_pause' ? 'paused' : 'playing' });
          return true;
        }

        const el = node(msg.id);
        if (cache.observations.get(msg.id) !== signature(el))
          throw new Error('That control or its form values changed. Take a fresh snapshot.');
        const expected = {
          fill: 'TYPE_TEXT',
          submit: 'SUBMIT',
          press_enter: 'PRESS_ENTER',
          select: 'SELECT',
          click: 'CLICK',
        }[cache.kinds.get(msg.id)];
        if (msg.op !== expected)
          throw new Error('The requested operation does not match this control.');
        if (
          !safe(el) ||
          !visible(el) ||
          el.matches(':disabled') ||
          el.closest('[aria-disabled="true"]')
        )
          throw new Error('That control is no longer available. Take a fresh snapshot.');
        el.scrollIntoView({ block: 'center', behavior: 'instant' });
        hitTest(el);
        // Show where it is going, then act, then flash - so a person can follow it.
        (async () => {
          await UI.point(
            el,
            (msg.op === 'TYPE_TEXT' ? 'Type: ' : msg.op === 'SELECT' ? 'Select: ' : 'Click: ') +
              name(el),
          );
          await UI.hit();
        })();
        if (msg.op === 'TYPE_TEXT') {
          el.focus();
          if (el.isContentEditable) {
            el.textContent = msg.text ?? '';
          } else {
            const setter = Object.getOwnPropertyDescriptor(el.constructor.prototype, 'value')?.set;
            setter ? setter.call(el, msg.text ?? '') : (el.value = msg.text ?? '');
          }
          el.dispatchEvent(new Event('input', { bubbles: true }));
          el.dispatchEvent(new Event('change', { bubbles: true }));
          reply({ ok: 'typed', label: name(el) });
        } else if (msg.op === 'SUBMIT') {
          const form = el.form;
          if (!form || !el.value) throw new Error('Fill this field before submitting its form.');
          const submitter = [...form.querySelectorAll('button,input[type="submit"]')].find(
            (button) => button.type === 'submit' && !button.disabled && visible(button),
          );
          // Native form submission respects validation and page submit handlers;
          // synthetic Enter key events do not trigger browser default actions.
          form.requestSubmit(submitter);
          reply({ ok: 'submitted', label: name(el) });
        } else if (msg.op === 'PRESS_ENTER') {
          if (!el.value) throw new Error('Fill this field before pressing Enter.');
          el.focus();
          for (const type of ['keydown', 'keypress', 'keyup'])
            el.dispatchEvent(
              new KeyboardEvent(type, {
                key: 'Enter',
                code: 'Enter',
                keyCode: 13,
                which: 13,
                bubbles: true,
                cancelable: true,
              }),
            );
          reply({ ok: 'Enter sent; verify the search results', label: name(el) });
        } else if (msg.op === 'SELECT') {
          el.value = msg.value;
          el.dispatchEvent(new Event('change', { bubbles: true }));
          reply({ ok: 'selected', value: msg.value });
        } else {
          if (typeof el.click === 'function') el.click();
          else {
            const rect = el.getBoundingClientRect();
            el.dispatchEvent(
              new MouseEvent('click', {
                bubbles: true,
                cancelable: true,
                composed: true,
                view: window,
                clientX: rect.x + rect.width / 2,
                clientY: rect.y + rect.height / 2,
              }),
            );
          }
          reply({ ok: 'clicked', label: name(el) });
        }
      } else if (msg.kind === 'text') {
        reply({ title: document.title, url: location.href, text: visibleText(8000) });
      }
    } catch (e) {
      reply({ error: String(e.message || e) });
    }
    return true;
  };
  window.__jevRevListener = listener;
  chrome.runtime.onMessage.addListener(listener);
})();
