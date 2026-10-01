# JevRev Claw: privacy and permissions

Claw connects only to a loopback PANINI address configured in its popup, and only after that app proves it holds this browser's pairing secret. It does not contain AI keys and does not connect directly to AI services. The local app sends requested page content and instructions to its configured AI providers. PANINI audio is handled by the app, not recorded by this extension.

The popup lists open website tabs locally. The relay receives metadata only for the selected tab or approved workspace. Page titles, addresses, rendered text and available controls are observed when the app requests them; screenshots may be requested for an active attached tab. Signed-in page content can be included, so attach only tabs you intend the agent to read and operate.

- `tabs`: list eligible pages and manage approved tab navigation/selection.
- `activeTab` and `scripting`: initialize and operate supported controls on an attached website.
- HTTP(S) host access: inject the content script on demand into attached tabs only, including already-open tabs and after navigation. Access does not authorize actions; an explicit attachment grant is still required.
- `storage`: save the local relay address and the pairing (a device ID and secret, used only to authenticate with PANINI), and hold attachment/workspace grants in browser-session storage.
- Optional `tabGroups`: create and label the workspace group. Requested when the user creates a workspace; single-tab mode does not need it.

The extension does not request cookies, browser history, microphone, debugger, downloads or native-messaging permissions. It refuses Chrome internal pages, Web Store pages and password/file/hidden field actions. It does not automatically adopt unrelated tabs or website-opened popups. The app's existing network, provider and logging rules also apply.

Detach to revoke control while keeping your pages open. Closing or moving an approved tab out revokes that tab. **Forget pairing** removes the pairing; removing the extension removes all its saved settings; app-side histories and provider retention are governed separately by the local app and configured provider policies.
