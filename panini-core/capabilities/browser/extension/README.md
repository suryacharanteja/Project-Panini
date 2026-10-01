# JevRev Claw: PANINI's browser extension

Claw lets PANINI and the web console operate the Chrome tabs you choose, signed in
as you. See [Browser capability](../../../../docs/capabilities/panini-browser.md) and
[privacy and permissions](PRIVACY.md).

## Install and pair (once per Chrome profile)

1. Start PANINI (`npm run panini`) or the runtime (`npm run dev`).
2. In `chrome://extensions`, enable Developer mode and **Load unpacked** this
   `panini-core/capabilities/browser/extension/` folder. After updating the code, click Reload there.
3. In PANINI choose **Pair browser extension**, or on the console's Browser page
   click **Pair JevRev Claw**. Type the 8-character code into the Claw popup and
   click **Pair with PANINI**.

Claw then reconnects by itself whenever PANINI runs, backing off to every 30
seconds while it is closed. The default address is `ws://127.0.0.1:3200/ws/extension`;
change it under **App connection** if you changed `FRONTEND_PORT`. **Forget
pairing** removes the pairing from this browser.

## Choose what PANINI may control

- **Single tab:** pick a website tab in the popup and click **Attach selected tab**.
- **Tab workspace:** select tabs from one window and click **Create workspace**.
  They form a yellow **JevRev** group. Enable **Allow the agent to open new tabs**
  only when wanted. Chrome asks for the optional `tabGroups` permission the first
  time. If the group loses its attachment (reload, restart), choose it under
  **Existing workspace** and click **Reconnect workspace**; no tabs are moved or reopened.

Actions run only in attached tabs. Detaching, switching or reconnecting
invalidates a running instruction's target, so it stops. Chrome settings, the
Web Store, password/file/hidden fields, CAPTCHAs and cross-origin frames are not
controlled. **Open demo website** opens a local practice page.

## What it can do

Navigate, read the attached page (including rendered text below the viewport),
click (including icon controls and open shadow roots), fill fields, submit forms,
press Enter in form-less search boxes, choose options, scroll and switch
workspace tabs. The app's planner proposes grounded actions from the page's
actual controls; Jev selects them; completion needs grounded evidence. In PANINI
every effect is confirmed in the avatar first. Watch the cursor highlights in the
attached tab.

## Security

Pairing gives this browser a device secret. On every connection Claw and PANINI
prove that secret to each other with an HMAC challenge; Claw refuses any command
from an app that cannot, so another program on the same port cannot drive your
tabs. The content script is injected only into attached tabs, and popup commands
are refused from web pages.

## Tests

Automated: `npm test` (background, popup and workspace tests). Real runtime
pairing and handshake: `npm run test:runtime`.
