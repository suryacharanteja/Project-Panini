// Full control of attached tabs for PANINI (V2-09): chrome.debugger on the granted tab only.
// Every frame passes the same tab-grant check as v1 actions (target()), every command passes the I9 method
// policy (cdp-policy.mjs, mirrored in PANINI's relay), and only events of attached tabs are forwarded.
// Chrome shows its "is debugging this browser" bar while attached; pressing Cancel there ends control.
import { decide } from './cdp-policy.mjs';

const VERSION = '1.3';

export function createCdp({ chrome, send, target, website }) {
  const attached = new Set();

  chrome.debugger.onEvent.addListener((source, method, params) => {
    if (!attached.has(source.tabId)) return;
    send({
      type: 'cdp_event',
      tab_id: source.tabId,
      session_id: source.sessionId ?? null,
      method,
      params: params ?? {},
    });
  });
  chrome.debugger.onDetach.addListener((source, reason) => {
    if (!attached.delete(source.tabId)) return;
    send({ type: 'cdp_detached', tab_id: source.tabId, reason: String(reason || 'detached') });
  });

  async function granted(action) {
    const tabId = await target(action);
    if (action.tab_id !== tabId)
      throw new Error('That tab is not the one you attached for PANINI.');
    const tab = await chrome.tabs.get(tabId);
    if (!website(tab.url))
      throw new Error('This page cannot be controlled (browser or Web Store page).');
    return tab;
  }

  async function handle(action) {
    switch (action.op) {
      case 'attach': {
        const tab = await granted(action);
        if (!attached.has(tab.id)) {
          try {
            await chrome.debugger.attach({ tabId: tab.id }, VERSION);
          } catch (e) {
            const message = String(e?.message || e);
            if (/another debugger/i.test(message))
              throw new Error(
                'DEVTOOLS_OPEN: Close DevTools on this tab, or PANINI will use basic controls.',
              );
            throw e;
          }
          attached.add(tab.id);
        }
        const info = await chrome.debugger.sendCommand({ tabId: tab.id }, 'Target.getTargetInfo');
        return { target_info: info?.targetInfo ?? {} };
      }
      case 'send': {
        const tab = await granted(action);
        if (!attached.has(tab.id)) throw new Error('Full control is not attached to this tab.');
        const [allowed, reason] = decide(action.method, action.params, {
          origin: new URL(tab.url).origin.toLowerCase(),
        });
        if (!allowed) throw new Error(reason);
        const result = await chrome.debugger.sendCommand(
          { tabId: tab.id, ...(action.session_id ? { sessionId: action.session_id } : {}) },
          action.method,
          action.params ?? {},
        );
        return result ?? {};
      }
      case 'detach': {
        if (attached.delete(action.tab_id))
          await chrome.debugger.detach({ tabId: action.tab_id }).catch(() => {});
        return { detached: true };
      }
      default:
        // Tab creation stays with the workspace's own controls in this version.
        throw new Error(`Unsupported control operation: ${action.op}`);
    }
  }

  async function detachAll() {
    for (const tabId of [...attached]) {
      attached.delete(tabId);
      await chrome.debugger.detach({ tabId }).catch(() => {});
    }
  }

  return { handle, detachAll, attached };
}
