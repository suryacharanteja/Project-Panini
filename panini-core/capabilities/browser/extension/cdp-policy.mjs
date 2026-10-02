// CDP method policy for PANINI in your Chrome (V2-09, invariant I9).
// The same table is enforced by PANINI's relay (backend/hands/cdp_policy.py); a test asserts both are
// identical. Unknown methods are denied. Pure module: no chrome.* APIs, so it is unit-tested with node --test.

export const TABLE = {
  allow_domains: [
    'Accessibility',
    'CSS',
    'DOM',
    'DOMSnapshot',
    'Emulation',
    'Input',
    'Inspector',
    'Log',
    'Overlay',
    'Page',
    'Performance',
    'Runtime',
  ],
  allow_methods: [
    'IO.close',
    'IO.read',
    'Network.disable',
    'Network.emulateNetworkConditions',
    'Network.enable',
    'Network.getResponseBody',
    'Network.setBypassServiceWorker',
    'Network.setCacheDisabled',
    'Network.setExtraHTTPHeaders',
    'Network.setUserAgentOverride',
    'Storage.getStorageKeyForFrame',
    'Target.getTargetInfo',
    'Target.setAutoAttach',
  ],
  deny_methods: [
    'Network.clearBrowserCache',
    'Network.clearBrowserCookies',
    'Network.deleteCookies',
    'Network.getAllCookies',
    'Network.setCookie',
    'Network.setCookies',
    'Page.setBypassCSP',
    'Page.setDownloadBehavior',
    'Target.attachToBrowserTarget',
    'Target.createBrowserContext',
    'Target.createTarget',
    'Target.disposeBrowserContext',
    'Target.exposeDevToolsProtocol',
    'Target.setDiscoverTargets',
  ],
  deny_domains: [
    'BackgroundService',
    'Browser',
    'Cast',
    'Debugger',
    'Extensions',
    'Fetch',
    'HeapProfiler',
    'Memory',
    'PWA',
    'Profiler',
    'Security',
    'ServiceWorker',
    'Storage',
    'SystemInfo',
    'Tethering',
    'Tracing',
    'WebAuthn',
  ],
  conditional: {
    'Fetch.continueRequest': 'network_tools',
    'Fetch.disable': 'network_tools',
    'Fetch.enable': 'network_tools',
    'Fetch.failRequest': 'network_tools',
    'Fetch.fulfillRequest': 'network_tools',
    'Network.getCookies': 'same_origin_cookies',
    'Page.navigate': 'web_url',
  },
};

function origin(url) {
  try {
    const parsed = new URL(String(url));
    return parsed.host ? `${parsed.protocol}//${parsed.host}`.toLowerCase() : '';
  } catch {
    return '';
  }
}

/** [allowed, reason] for one CDP command to the attached tab. */
export function decide(method, params = {}, scope = {}) {
  params = params || {};
  scope = scope || {};
  const rule = TABLE.conditional[method];
  if (rule === 'web_url') {
    const url = String(params.url || '');
    let scheme = '';
    try {
      scheme = new URL(url).protocol.replace(':', '');
    } catch {}
    if (url === 'about:blank' || scheme === 'http' || scheme === 'https') return [true, ''];
    return [false, 'Only http(s) pages can be opened in your Chrome.'];
  }
  if (rule === 'same_origin_cookies') {
    const urls = Array.isArray(params.urls) ? params.urls : [];
    if (scope.origin && urls.length && urls.every((u) => origin(u) === scope.origin))
      return [true, ''];
    return [false, "Cookies outside the attached tab's site are not available."];
  }
  if (rule === 'network_tools')
    return scope.network_tools
      ? [true, '']
      : [false, 'Network interception is off for your Chrome.'];
  if (TABLE.deny_methods.includes(method))
    return [false, `${method} is not allowed in your Chrome.`];
  if (TABLE.allow_methods.includes(method)) return [true, ''];
  if (TABLE.allow_domains.includes(String(method).split('.')[0])) return [true, ''];
  return [false, `${method} is not allowed in your Chrome.`];
}
