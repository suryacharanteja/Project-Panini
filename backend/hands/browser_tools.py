"""Browser vocabulary v2 (V2-10): every Playwright MCP 1.63 tool, with its effect class, the group it is offered
in, and its availability per browser. The manifest (`panini-core/capabilities/browser/manifests/22-browser.json`)
is generated from this table and Playwright's own schemas (`scripts/browser-manifest.py`), so the model sees the
tool names and arguments it already knows from Playwright.

Availability: "allow" (normal policy), "tap" (the person approves every call there), "never" (refused there).
"""

from dataclasses import dataclass, field

BROWSERS = ("chrome", "managed", "isolated")
A, T, N = "allow", "tap", "never"


@dataclass(frozen=True)
class BrowserTool:
    name: str
    effect: str
    group: str = "core"
    chrome: str = A
    managed: str = A
    isolated: str = A
    dynamic: bool = False  # The policy raises the effect from the element's name, the dialog or the keys.
    ref: bool = False  # Acts on an element ref from the latest snapshot (stale-ref guard).
    typing: bool = False  # Typed text: the password probe runs first (I5).
    evidence: str = ""  # "assertion" for verify tools, "page" for observations.
    offered: bool = True
    title: str = ""
    extra: dict = field(default_factory=dict)

    def availability(self, browser: str) -> str:
        return getattr(self, browser)


def _t(name, effect, **kw):
    return BrowserTool(name, effect, **kw)


TOOLS: dict[str, BrowserTool] = {
    t.name: t
    for t in [
        # Reading and moving around.
        _t("browser_snapshot", "observe", evidence="page", title="Read the page"),
        _t("browser_find", "observe", title="Find on the page"),
        _t("browser_take_screenshot", "observe", evidence="page", title="Screenshot"),
        _t("browser_navigate", "navigate", title="Open a page"),
        _t("browser_navigate_back", "navigate", title="Go back"),
        _t("browser_tabs", "navigate", title="Tabs"),
        _t("browser_wait_for", "observe", title="Wait"),
        _t("browser_hover", "navigate", ref=True, title="Hover"),
        _t("browser_generate_locator", "observe", ref=True, title="Locator for an element"),
        _t("browser_highlight", "observe", ref=True, title="Highlight an element"),
        _t("browser_hide_highlight", "observe", title="Hide the highlight"),
        _t(
            "browser_verify_element_visible",
            "observe",
            evidence="assertion",
            title="Check an element is visible",
        ),
        _t("browser_verify_list_visible", "observe", evidence="assertion", title="Check a list is visible"),
        _t("browser_verify_text_visible", "observe", evidence="assertion", title="Check text is visible"),
        _t("browser_verify_value", "observe", evidence="assertion", title="Check a field's value"),
        # Acting.
        _t("browser_click", "submit", dynamic=True, ref=True, title="Click"),
        _t("browser_type", "input", dynamic=True, ref=True, typing=True, title="Type"),
        _t("browser_fill_form", "input", dynamic=True, typing=True, title="Fill a form"),
        _t("browser_select_option", "input", ref=True, title="Choose an option"),
        _t("browser_press_key", "input", dynamic=True, title="Press a key"),
        _t("browser_drag", "input", dynamic=True, title="Drag"),
        _t("browser_drop", "input", dynamic=True, title="Drop"),
        _t("browser_handle_dialog", "submit", dynamic=True, title="Answer a dialog"),
        _t("browser_file_upload", "share_out", chrome=T, managed=T, isolated=T, title="Upload files"),
        _t("browser_pdf_save", "local_write", title="Save the page as PDF"),
        # Developer views (offered when the task needs them).
        _t("browser_console_messages", "observe", group="browser_devtools", title="Console messages"),
        _t("browser_network_requests", "observe", group="browser_devtools", title="Network requests"),
        _t("browser_network_request", "observe", group="browser_devtools", title="One network request"),
        _t(
            "browser_evaluate",
            "local_exec",
            group="browser_devtools",
            chrome=N,
            managed=T,
            isolated=T,
            title="Run page JavaScript",
        ),
        # Coordinates (canvas pages and the vision tier).
        _t(
            "browser_mouse_click_xy", "submit", group="browser_vision", dynamic=True, title="Click at a point"
        ),
        _t(
            "browser_mouse_drag_xy",
            "submit",
            group="browser_vision",
            dynamic=True,
            title="Drag between points",
        ),
        _t("browser_mouse_down", "submit", group="browser_vision", dynamic=True, title="Mouse down"),
        _t("browser_mouse_up", "submit", group="browser_vision", dynamic=True, title="Mouse up"),
        _t("browser_mouse_move_xy", "navigate", group="browser_vision", title="Move the mouse"),
        _t("browser_mouse_wheel", "navigate", group="browser_vision", title="Scroll the wheel"),
    ]
}

# Storage and network control: only PANINI's own profiles, never offered to the model in this version.
for _name in (
    "browser_cookie_list",
    "browser_cookie_get",
    "browser_localstorage_list",
    "browser_localstorage_get",
    "browser_sessionstorage_list",
    "browser_sessionstorage_get",
):
    TOOLS[_name] = _t(_name, "observe", group="browser_storage", chrome=N, offered=False)
for _name in (
    "browser_cookie_set",
    "browser_cookie_delete",
    "browser_cookie_clear",
    "browser_localstorage_set",
    "browser_localstorage_delete",
    "browser_localstorage_clear",
    "browser_sessionstorage_set",
    "browser_sessionstorage_delete",
    "browser_sessionstorage_clear",
    "browser_set_storage_state",
):
    TOOLS[_name] = _t(_name, "account", group="browser_storage", chrome=N, managed=T, offered=False)
TOOLS["browser_storage_state"] = _t(
    "browser_storage_state", "local_write", group="browser_storage", chrome=N, managed=T, offered=False
)
for _name in ("browser_route", "browser_route_list", "browser_unroute", "browser_network_state_set"):
    TOOLS[_name] = _t(_name, "submit", group="browser_network", chrome=N, managed=T, offered=False)
# The person's and the wrapper's own tools: never offered to models.
for _name in (
    "browser_start_tracing",
    "browser_stop_tracing",
    "browser_start_video",
    "browser_stop_video",
    "browser_video_chapter",
    "browser_video_show_actions",
    "browser_video_hide_actions",
    "browser_start_recording",
    "browser_stop_recording",
    "browser_annotate",
    "browser_resume",
    "browser_close",
    "browser_resize",
):
    TOOLS[_name] = _t(_name, "observe", group="owner", chrome=N, offered=False)
TOOLS["browser_run_code_unsafe"] = _t(
    "browser_run_code_unsafe", "local_exec", group="owner", chrome=N, managed=N, isolated=T, offered=False
)

# PANINI tools beside Playwright's.
PANINI_TOOLS = {
    "browser_select": "navigate",
    "browser_status": "observe",
    "browser_media": "input",
}

PRIVILEGED_PROBES = {
    # Fixed scripts the wrapper runs; never model-supplied (V2-03 §5.7).
    "field_info": "(el) => ({tag: el.tagName, type: (el.type || '').toLowerCase(), autocomplete: (el.autocomplete || '').toLowerCase(), name: el.name || '', id: el.id || ''})",
    "media_state": """() => {
      const video = [...document.querySelectorAll('video')].sort((a, b) => b.clientWidth * b.clientHeight - a.clientWidth * a.clientHeight)[0];
      const ad = document.querySelector('.ad-showing, .ytp-ad-player-overlay, .ytp-ad-module .ytp-ad-text');
      const skip = document.querySelector('.ytp-skip-ad-button, .ytp-ad-skip-button, .ytp-ad-skip-button-modern');
      return {found: !!video, playing: !!video && !video.paused && !video.ended, ad_showing: !!ad, ad_skippable_now: !!skip && skip.offsetParent !== null};
    }""",
    "media_play": "() => { const v = [...document.querySelectorAll('video')][0]; if (!v) return {ok: false}; v.play(); return {ok: true}; }",
    "media_pause": "() => { const v = [...document.querySelectorAll('video')][0]; if (!v) return {ok: false}; v.pause(); return {ok: true}; }",
    "media_skip_ad": "() => { const b = document.querySelector('.ytp-skip-ad-button, .ytp-ad-skip-button, .ytp-ad-skip-button-modern'); if (!b) return {ok: false}; b.click(); return {ok: true}; }",
}

CREDENTIAL_AUTOCOMPLETE = ("password", "one-time-code", "cc-number", "cc-csc", "cc-exp")
SENSITIVE_HEADERS = (
    "cookie",
    "set-cookie",
    "authorization",
    "proxy-authorization",
    "x-api-key",
    "x-auth-token",
)
