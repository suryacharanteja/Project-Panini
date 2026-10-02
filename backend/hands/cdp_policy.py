"""CDP method policy for your Chrome (V2-09, invariant I9).

The same table is enforced by the relay here and by JevRev Claw (`extension/cdp-policy.js`); a test asserts the
two tables are identical. Unknown methods are denied. The rules keep Playwright working inside the attached tab
while refusing anything that reaches beyond it: all cookies, other sites' storage, browser settings, other
contexts, debugging the browser itself.
"""

from urllib.parse import urlsplit

TABLE = {
    # Tab-scoped domains: the debugger session is bound to the attached tab.
    "allow_domains": [
        "Accessibility",
        "CSS",
        "DOM",
        "DOMSnapshot",
        "Emulation",
        "Input",
        "Inspector",
        "Log",
        "Overlay",
        "Page",
        "Performance",
        "Runtime",
    ],
    "allow_methods": [
        "IO.close",
        "IO.read",
        "Network.disable",
        "Network.emulateNetworkConditions",
        "Network.enable",
        "Network.getResponseBody",
        "Network.setBypassServiceWorker",
        "Network.setCacheDisabled",
        "Network.setExtraHTTPHeaders",
        "Network.setUserAgentOverride",
        "Storage.getStorageKeyForFrame",
        "Target.getTargetInfo",
        "Target.setAutoAttach",
    ],
    "deny_methods": [
        "Network.clearBrowserCache",
        "Network.clearBrowserCookies",
        "Network.deleteCookies",
        "Network.getAllCookies",
        "Network.setCookie",
        "Network.setCookies",
        "Page.setBypassCSP",
        "Page.setDownloadBehavior",
        "Target.attachToBrowserTarget",
        "Target.createBrowserContext",
        "Target.createTarget",
        "Target.disposeBrowserContext",
        "Target.exposeDevToolsProtocol",
        "Target.setDiscoverTargets",
    ],
    # Documented for review; anything not allowed above is denied anyway.
    "deny_domains": [
        "BackgroundService",
        "Browser",
        "Cast",
        "Debugger",
        "Extensions",
        "Fetch",
        "HeapProfiler",
        "Memory",
        "PWA",
        "Profiler",
        "Security",
        "ServiceWorker",
        "Storage",
        "SystemInfo",
        "Tethering",
        "Tracing",
        "WebAuthn",
    ],
    # Methods allowed only when their parameters stay inside the tab.
    "conditional": {
        "Fetch.continueRequest": "network_tools",
        "Fetch.disable": "network_tools",
        "Fetch.enable": "network_tools",
        "Fetch.failRequest": "network_tools",
        "Fetch.fulfillRequest": "network_tools",
        "Network.getCookies": "same_origin_cookies",
        "Page.navigate": "web_url",
    },
}

NAVIGABLE = ("http", "https")


def decide(method: str, params: dict | None, scope: dict | None = None) -> tuple[bool, str]:
    """(allowed, reason) for one CDP command from Playwright to the attached tab."""
    params, scope = params or {}, scope or {}
    rule = TABLE["conditional"].get(method)
    if rule == "web_url":
        url = str(params.get("url", ""))
        if url == "about:blank" or urlsplit(url).scheme in NAVIGABLE:
            return True, ""
        return False, "Only http(s) pages can be opened in your Chrome."
    if rule == "same_origin_cookies":
        origin = scope.get("origin") or ""
        urls = params.get("urls") or []
        if origin and urls and all(_origin(u) == origin for u in urls):
            return True, ""
        return False, "Cookies outside the attached tab's site are not available."
    if rule == "network_tools":
        return (
            (True, "")
            if scope.get("network_tools")
            else (False, "Network interception is off for your Chrome.")
        )
    if method in TABLE["deny_methods"]:
        return False, f"{method} is not allowed in your Chrome."
    if method in TABLE["allow_methods"]:
        return True, ""
    if method.split(".", 1)[0] in TABLE["allow_domains"]:
        return True, ""
    return False, f"{method} is not allowed in your Chrome."


def _origin(url: str) -> str:
    parts = urlsplit(str(url))
    return f"{parts.scheme}://{parts.netloc}".lower() if parts.scheme and parts.netloc else ""


def origin_of(url: str) -> str:
    return _origin(url)
