# V2-21: Android hands

| Field | Value |
| --- | --- |
| Phase / priority / size | Phase 5 · P2 · L (1.5 weeks) |
| Depends on | V2-08 (Node sidecar pattern), V2-03 (policy), V2-12 (live view) |
| Flag | `PANINI_ANDROID` (default `false`) |
| Status | Not started |
| Decision | Register D6 (ADB wireless debugging first) |

## 1. Outcome

PANINI can operate apps on your Android phone the way it operates Windows apps: read what is on screen, tap,
type, swipe, open apps and links, with the same policy and approvals, a live view on the PC, and a visible
notice on the phone while it is in control. Example: "On my phone, open the parking app and extend today's
session" (with your approval for the payment step).

## 2. Scope and non-goals

- In scope: ADB setup (wireless debugging pairing or USB), a Node sidecar on Playwright's experimental
  `_android` API exposing an MCP tool set, UI snapshots with refs from `uiautomator`, actions by selector or
  bounds, app launch and URL open, screenshots and live frames, policy (classification, password fields,
  sensitive apps), Admin setup wizard.
- Not in scope: iOS (no automation API; Shortcuts only), a native Android companion with an accessibility
  service (phase b, after the PWA/native app work in V2-15 §5.7), rooted-device features.

## 3. Current state

No phone control. The phone app (V2-15) is a remote control for the PC, not a hand.

## 4. Harvest

| Source | What we take | Adaptation |
| --- | --- | --- |
| Playwright 1.63 `_android` (types in `playwright-core/types/types.d.ts`) | `android.devices({host, port, omitDriverInstall})`; `AndroidDevice`: `tap/longTap/fill/press/swipe/scroll/fling/drag/pinchOpen/pinchClose/wait/info(selector)`, `screenshot()`, `shell(cmd)`, `installApk`, `push`, `launchBrowser()` (a Playwright context on Chrome for Android), `webViews()`; `AndroidInput`: `tap(point)`, `swipe`, `drag`, `type(text)`, `press(key)`; `AndroidSelector`: `checkable, checked, clazz, clickable, depth, desc, enabled, focusable, focused, hasChild, hasDescendant, longClickable, pkg, res, scrollable, selected, text` | `panini-core/capabilities/android/sidecar/android-mcp.mjs` |
| Playwright aria-snapshot grammar (`- button "Send" [ref=e5]`) | Compact tree with refs | Same grammar for Android nodes (and V2-26 for every hand) |
| PANINI Desktop (copper cursor, approvals on effects, postconditions) | Visible, approved, verified actions | Same contract on Android |

## 5. Design

### 5.1 Components and files

| File | Purpose |
| --- | --- |
| `panini-core/capabilities/android/sidecar/android-mcp.mjs` | Minimal MCP server (initialize, tools/list, tools/call) over stdio using `playwright-core` `_android` |
| `panini-core/capabilities/android/sidecar/uitree.mjs` | `uiautomator dump` XML → compact tree with refs and bounds |
| `panini-core/capabilities/android/panini.capability.json` | Manifest: `android` capability, hand per device serial |
| `backend/hands/android.py` | Hand wrapper: policy observations, probes, live frames |
| `backend/hands/adb.py` | ADB discovery, optional platform-tools download (with consent), pairing and connect helpers |
| Admin `src/pages/Android.tsx` | Wizard: enable developer options → wireless debugging → pairing code → connect → test |

### 5.2 Setup

1. Find `adb` on `PATH` or in `%LOCALAPPDATA%\PANINI\platform-tools`; if missing, offer to download Android
   SDK Platform-Tools from Google's official URL (shows file name and size; Apache-2.0).
2. On the phone: Developer options → Wireless debugging → "Pair device with pairing code". In Admin enter
   the IP:port and the six-digit code → `adb pair`; then `adb connect <ip:port>`. USB debugging also works.
3. First `android.devices()` installs Playwright's small driver apps on the phone (Playwright does this unless
   `omitDriverInstall`); Admin explains this before continuing.
4. Test: snapshot of the home screen shown in Admin.

### 5.3 Snapshot with refs

`android_snapshot` runs `uiautomator dump /dev/tty` through `device.shell`, parses the XML and emits:

```text
- app "com.whatsapp" [ref=a1]
  - toolbar "Chats" [ref=a2]
  - list [ref=a3]
    - listitem "Mum, Running late, 10:41" [ref=a4] [box=0,312,1080,140]
  - button "New chat" [ref=a5] [box=900,2000,150,150]
```

Role from `class` (`Button`, `EditText → textbox`, `CheckBox`, `Switch`, `ImageButton → button`, `TextView →
text`, `RecyclerView/ListView → list`, …), name from `text` or `content-desc`, state flags (`checked`,
`enabled`, `selected`, `password`), `resource-id` kept internally for selectors. Refs are valid until the
next snapshot (stale-ref rule as V2-10).

### 5.4 Tools

| Tool | Effect | Implementation |
| --- | --- | --- |
| `android_devices()` | observe | serials, models |
| `android_snapshot(depth?)` | observe | §5.3; taints `android` |
| `android_screenshot()` | observe | `device.screenshot()`; artifact; black frames on `FLAG_SECURE` screens are reported as such |
| `android_tap(ref)` / `android_long_tap(ref)` | submit dyn | Selector from `res` + `text`/`desc` when unique, else the ref's bounds centre with `device.input.tap` |
| `android_fill(ref, text)` | input | `device.fill(selector, text)`; refused on `password=true` nodes (credential) |
| `android_press(key)` | input (Enter dyn) | `device.press`/`input.press` (`Back`, `Home`, `AppSwitch`, `Enter`, …) |
| `android_swipe(direction, ref?)`, `android_scroll(ref, direction)` | navigate | `device.swipe/scroll` |
| `android_launch(package)` | navigate | `shell("monkey -p <pkg> -c android.intent.category.LAUNCHER 1")` |
| `android_open_url(url)` | navigate | `shell("am start -a android.intent.action.VIEW -d <url>")` (http/https only) |
| `android_wait(selector_text, timeout)` | observe | `device.wait` |
| `android_verify_text(text)` | observe (assertion) | snapshot contains text → evidence |

`shell` is never exposed to the model.

### 5.5 Policy

- Classification from the node's name, `resource-id` and app (`Send`, `Pay`, `Buy`, `Delete`, `Call`, `Post`,
  `Transfer`, `Confirm` …) using V2-03 word classes; `communicate` covers messaging apps' send buttons and the
  dialer's call button.
- **Sensitive apps** (deny by default; owner can grant per app): banking and payment apps, password managers,
  authenticator apps, system settings for accounts and security, app stores (install). The list is a
  package-prefix table in the manifest plus an Admin editable list.
- Password fields (`password="true"`) → credential, always denied.
- While PANINI controls the phone, Android shows its debugging notification; PANINI also posts a notice
  through the phone app (V2-15): "PANINI is using your phone. Tap to stop."
- Remote use (the person away from the PC and phone in hand) uses the phone app's Stop; physical touch on the
  phone does not pause PANINI automatically (ADB cannot detect it), so runs re-snapshot before every effect.

### 5.6 Failure modes

| Failure | Behaviour |
| --- | --- |
| Device disconnected (Wi-Fi change, sleep) | `blocked` "Your phone is not connected"; Admin reconnect button |
| `uiautomator dump` fails (secure screens, animations) | One retry after 500 ms; then screenshot-only observation with `vision` tier |
| Driver install refused | Readiness `needs_setup` with the reason |

## 6. Implementation steps

1. `adb.py` discovery, optional download, pair/connect helpers; Admin wizard skeleton.
2. `android-mcp.mjs` with `devices`, `snapshot`, `screenshot`; `uitree.mjs` parser with fixtures.
3. Action tools; selector-or-bounds strategy; stale-ref guard.
4. Hand wrapper with policy observations, sensitive apps, password refusal; manifest.
5. Live frames (1 fps while watched) and the phone notice.
6. Golden tasks on a real device: open Settings → Wi-Fi status; read the latest notification; send a message
   to yourself (approval).

## 7. Tests

| Level | Test | File |
| --- | --- | --- |
| Unit | XML → tree with roles, names, refs, bounds (recorded dumps from 3 apps) | `panini-core/capabilities/android/test/uitree.test.mjs` |
| Unit | Policy: send buttons → communicate; sensitive apps denied; password fields denied | `backend/tests/hands/test_android_policy.py` |
| Integration | Fake device (mocked `_android`) end to end through the registry | `backend/tests/hands/test_android_hand.py` |
| Live | Real phone over wireless debugging: the three golden tasks | evidence log |

## 8. Acceptance criteria

- [ ] Wireless-debugging pairing works from Admin; the phone appears as a hand.
- [ ] Golden tasks pass; the message send requires approval; banking apps are refused.
- [ ] Live view shows the phone screen on the PC.

## 9. Rollout and rollback

Opt-in per device. Rollback: flag off; `adb disconnect`; Playwright's driver apps can be uninstalled from the
phone (Admin shows how).

## 10. Open questions

- Use `launchBrowser()` to drive Chrome on Android with the full Playwright browser vocabulary? Recommendation:
  yes in a follow-up, reusing V2-10's tool table with `browser_select("android")`.
