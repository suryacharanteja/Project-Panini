# PANINI Browser

## Implemented

- **Two browser executors under one capability.** PANINI Browser includes both the managed Browser Use/Chromium executor and the signed-in-tab Chrome extension executor.
- **Your Chrome by default when attached.** Tasks run in the tab (or tab workspace) you attached in JevRev Claw, with your logins. A separate managed Chromium session is used only when you ask for one ("use a separate browser") or choose it in the console.
- **Managed Browser Use when isolation is needed.** The `chromium` mode uses PANINI's managed Browser Use loop for a separate browser session.
- Navigation, DOM-grounded controls, text input, supported key presses, scrolling, tab switching, extraction, screenshots and dropdowns.
- Admission before every action, confirmation of effects in the PANINI shell, target identity checks and verification evidence.
- The console and PANINI share one executor; a busy browser reports who is using it.

## Setup

1. Configure `OPENAI_API_KEY` and `AI_GATEWAY_API_KEY`.
2. Load `panini-core/capabilities/browser/extension/` in `chrome://extensions` (Developer mode → Load unpacked).
3. In PANINI choose **Pair browser extension** (or **Pair JevRev Claw** on the console's Browser page) and type the code into the extension popup. Pairing is once per Chrome profile and survives restarts.
4. In the popup, attach a website tab or create a tab workspace.

If Chrome is not paired, connected or attached, PANINI names the missing step instead of opening another browser.

For managed Browser Use mode, choose **Separate browser window · managed session** in the browser console or ask PANINI to use a separate/managed browser.

## Security

The extension and the runtime prove the same pairing secret to each other on every connection (HMAC challenge-response, the secret is never sent). An unpaired program cannot pose as the extension, and a program listening on the runtime's port cannot command it. Popup commands are refused from content scripts. The content script is injected only into attached tabs. **Forget pairing** in the popup, or `DELETE /api/extension/devices/{id}` from the console, revokes a device.

## Limits and validation

Password entry requires user takeover. The managed profile rejects unsupported actions and coordinate clicks without DOM indices; graphical Windows input belongs to Desktop. Website content is untrusted data, not permission.

Source: `panini-core/capabilities/browser/runtime/panini_browser/`, `panini-core/capabilities/browser/extension/`, `backend/capabilities/browser.py`, and `backend/routes/browser.py`. The backend `extension_*`, `chat_jev`, and `jev_browser_loop` modules are compatibility shims into the capability runtime. Offline: `npm test`. Runtime: `npm run test:runtime` (pairing and handshake). Live: `npm run panini:smoke -- --browser` against the local demonstration page.
