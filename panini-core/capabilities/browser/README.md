# PANINI Browser capability

PANINI Browser is not “just the extension.”

PANINI Browser is the full browser-control capability used by the PANINI assistant. It has two execution adapters under one product boundary:

1. **Managed Browser Use / Chromium mode** for autonomous browser tasks in a separate PANINI-controlled browser session.
2. **Signed-in Chrome extension mode** for operating an explicitly attached user Chrome tab or tab workspace without stealing authority over the whole browser.

The extension is therefore one adapter inside PANINI Browser, not the definition of PANINI Browser.

## Capability model

```text
PANINI Browser
├─ capability adapter and target selection
│  └─ backend/capabilities/browser.py
├─ API routes, leases, cancellation and event streaming
│  └─ backend/routes/browser.py
├─ managed Browser Use executor
│  ├─ runtime/panini_browser/jev_browser_loop.py
│  ├─ runtime/panini_browser/chat_jev.py
│  └─ browser_use package integration
├─ signed-in-tab extension executor
│  ├─ runtime/panini_browser/extension_bridge.py
│  ├─ runtime/panini_browser/extension_loop.py
│  ├─ runtime/panini_browser/extension_pairing.py
│  └─ extension/
├─ browser console UI
│  └─ frontend/public/browser-use.*
└─ shell/admin integration
   └─ panini-core/apps/shell/, panini-core/apps/admin/
```

## Modes

| Mode        | Purpose                                                                 | Main implementation                                                                               |
| ----------- | ----------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------- |
| `chromium`  | Separate managed Browser Use session for autonomous browsing.           | `runtime/panini_browser/jev_browser_loop.py`, `runtime/panini_browser/chat_jev.py`, `browser_use` |
| `extension` | User-owned signed-in Chrome tab/workspace with explicit pairing/attach. | `runtime/panini_browser/extension_*.py`, `extension/`                                             |
| shared      | Routing, target choice, leases, Stop, evidence, status and UI events.   | `backend/capabilities/browser.py`, `backend/routes/browser.py`, `frontend/browser-use`            |

## Production rules

- Never silently switch authority. If the user selected an attached Chrome tab, use that tab or stop with a clear reason.
- Treat Browser Use and the extension as peer adapters behind the same PANINI Browser capability.
- Keep graphical Windows input in PANINI Desktop; keep browser DOM/navigation/session work in PANINI Browser.
- Preserve observable, durable, replayable, honestly verified actions for both modes.

## Validation

Offline coverage is included in:

```powershell
npm test
```

Runtime/live checks include:

```powershell
npm run test:runtime
npm run panini:smoke -- --browser
.venv\Scripts\python.exe scripts\verify-extension.py
```
