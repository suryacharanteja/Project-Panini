# V2-15: Phone app v2 (approvals, passkeys, push, share, live)

| Field | Value |
| --- | --- |
| Phase / priority / size | Phase 3 · P0 · L (1.5 weeks) |
| Depends on | V2-14 (fabric), V2-12 (live view), V2-05 (interrupts) |
| Flag | Active when `PANINI_REMOTE_MODE` ≠ `off` |
| Status | In development: Android PWA pairing, screens, signed decisions and browser smoke verified; physical-device qualification pending |

## 1. Outcome

Your phone becomes PANINI's remote control and approval device: a notification says exactly what PANINI
wants to do, one tap approves everyday actions, Face ID or a fingerprint approves high-risk ones (and that
signature is bound to the exact action), you can continue any conversation, share anything from other apps
to PANINI, watch the live view and press Stop, from anywhere.

## 2. Scope and non-goals

- In scope: an installable PWA served over the fabric (`panini-core/apps/phone/`), screens (Home, Inbox,
  Ask, Threads, Live, Settings), push handling with actionable notifications, passkeys (WebAuthn) for
  `verify`-level decisions, share target, photo and voice input, offline behaviour, iOS notes, the native
  app plan.
- Not in scope: a native app build (designed for, built later), Android automation (V2-21), watch apps
  (V2-24 uses notification mirroring).

## 3. Current state

`backend/harness/mobile_app/` (`index.html`, `app.js` 151 lines, `app.css`, `manifest.webmanifest`,
`icon.svg`): pair with a code, see status and the inbox, decide reviews (`rerun|dismiss`), dismiss notices,
send a request (runs as a background task, least privilege). Served by `mobile.py` with a strict CSP.

## 4. Harvest

| Source | What we take | Adaptation |
| --- | --- | --- |
| OpenDots `src/client/CallView.tsx`, `useVoice.ts` | Call screen with timer, separate captions, mute, speaker mute, minimize to chat | Live screen layout; voice dictation and (later) in-app calls reuse the WebRTC voice path |
| OpenDots `PageReviewCard.tsx` | Restore-before-decide; "Nothing is saved until you approve" copy | Interrupt cards on the phone |
| OpenDots `ResultPane.tsx`, `ChatTranscript.tsx`, `TaskActions.tsx` | Timeline with work updates, receipts, send → Stop | Threads screen |
| OpenMuse UX (`PANINI-AGENTIC-DESIGN.md` §5): follow-up queue, background updates linking to the saved task, review screens with exact recipient and effect, editable identity and memory | Phone UX patterns | Inbox and Threads |
| W3C Web Share Target, Web Push, WebAuthn | Platform features | §5.4–5.6 |

## 5. Design

### 5.1 Components and files

| File | Purpose |
| --- | --- |
| `panini-core/apps/phone/` | Plain ES modules + Web Components (no framework; small, auditable, signed per V2-14 §5.7) |
| `phone/sw.js` | App-shell cache, signed-update verification, push and notification actions, share-target capture, background sync of drafts |
| `phone/fabric.js` | Envelope client (shared test vectors with `backend/fabric/envelope.py`) |
| `phone/screens/{home,inbox,ask,threads,live,settings}.js` | Screens |
| `phone/cards/` | Interrupt, question, choice, review, takeover, receipt cards (same semantics as `shared-ui`) |
| `backend/fabric/passkeys.py` | WebAuthn registration and assertion verification (`webauthn` package, BSD-3-Clause) |
| `backend/harness/migrations/0010_passkeys.sql` | `passkeys` |
| `backend/harness/mobile_app/` | Removed after migration; `lan` mode serves the new app |

### 5.2 Screens

| Screen | Contents |
| --- | --- |
| Home | PANINI state (idle/working/paused), active runs with **Stop**, **Pause PANINI**, last result, connection indicator (transport and PC online) |
| Inbox | Open interrupts first (approval, question, choice, review, takeover) with previews; notices; background results |
| Ask | Text box, voice dictation (Web Speech API where available, else record → `/api/stt/transcribe` through the fabric), attach a photo (camera) or file; choose persona (V2-18) |
| Threads | Conversations from every surface; continue with a turn (foreground run, principal `phone_app`) |
| Live | V2-12 LiveView: frames at ≤ 1 fps, URL/title, **Take control / Give back**, tap-to-click and keyboard for sandbox and managed browsers |
| Settings | Device name, notifications test, passkey enrol/remove, transport details, **Forget this phone** |

### 5.3 Decisions on the phone

| Level | How the phone resolves |
| --- | --- |
| `tap` | **Approve/Decline** buttons on the card, or the notification's action buttons (Android); the request is signed by the device key (V2-14 §5.8) |
| `verify` | The card asks for a passkey: `navigator.credentials.get({publicKey: {challenge, allowCredentials, userVerification: "required", rpId}})`; `challenge = SHA-256(interrupt_id ‖ nonce ‖ action_digest ‖ decision)` so the biometric signature authorizes exactly this action and decision |
| `local` | Not resolvable on the phone; the card says "Approve this on your PC" |

The PC verifies the WebAuthn assertion (origin, RP id, challenge, signature, sign-count, user-verified flag)
before resolving; `decided_by.method = "passkey"`.

RP id: the transport origin's host (`<pc>.<tailnet>.ts.net` or the relay host). WebAuthn does not work on
`http://` LAN IPs, so `verify` interrupts are not resolvable in `lan` mode (the card explains why).

```sql
CREATE TABLE IF NOT EXISTS passkeys(credential_id TEXT PRIMARY KEY, device_id TEXT NOT NULL, rp_id TEXT NOT NULL,
  public_key BLOB NOT NULL, sign_count INTEGER NOT NULL, created REAL NOT NULL, last_used REAL);
```

### 5.4 Push and notifications

- `sw.js` handles `push`: shows a notification with the interrupt title and level; for `tap`, Android
  notifications get **Approve** and **Decline** actions; for `verify`, tapping opens the card.
- Notification actions run in the service worker: it loads the device key from IndexedDB, opens a fabric
  session, fetches the interrupt (restore-before-decide), and posts the signed decision. If anything changed
  (expired, already decided, digest differs) it shows a follow-up notification instead of retrying.
- iOS: actions on web notifications are not available; tapping opens the app. Push needs the app on the Home
  Screen (iOS 16.4+); the onboarding screen says so.

### 5.5 Share target

Manifest:

```json
"share_target": {"action": "/share", "method": "POST", "enctype": "multipart/form-data",
  "params": {"title": "title", "text": "text", "url": "url", "files": [{"name": "files", "accept": ["image/*", "application/pdf", "text/*"]}]}}
```

`sw.js` intercepts `POST /share`, stores the payload in IndexedDB, opens the Ask screen pre-filled, and sends
it over the fabric when the person confirms (or immediately with the default action from Settings: "Ask
what to do" (default), "Save to Inbox Space" (V2-19), "Summarize"). Shared files become artifacts on the PC;
shared content is tainted (`channel`-equivalent label `shared`). Android Chrome supports share targets for
installed PWAs; iOS does not (an iOS Shortcut can post a link through a tailnet-only token, documented).

### 5.6 Offline and failures

| Situation | Behaviour |
| --- | --- |
| PC asleep or offline | Home shows "Your PC is offline"; drafts stay on the phone and are sent when it is back |
| Session expired | Silent re-handshake |
| Device revoked | App shows "This phone was removed from PANINI" and deletes its keys |
| Update not signed by the PC | Keeps the old version; banner explains |

### 5.7 Native app (later)

The API surface is the fabric router (V2-14 §5.8), so a native app (Expo, as OpenMuse used) can be added for
features the web cannot do: share sheet on iOS, widgets, background location for triggers (V2-25), Android
accessibility hands (V2-21 phase b). Keys move to the platform keystore; passkeys remain the `verify` method.

## 6. Implementation steps

1. Scaffold `apps/phone` with the envelope client, pairing and SAS screens (with V2-14).
2. Home, Inbox (cards with restore-before-decide), Threads, Ask (text).
3. Push: subscription, notification display, Android actions, signed decisions from the service worker.
4. Passkeys: enrol in Settings; `verify` decisions; PC verification; tests with recorded assertions.
5. Live screen with takeover controls.
6. Share target, photo and file upload, voice dictation.
7. Signed-update flow; offline drafts; migration from `mobile_app/`.

## 7. Tests

| Level | Test | File |
| --- | --- | --- |
| Unit | Card rendering and restore-before-decide logic (no DOM framework needed) | `panini-core/apps/phone/test/cards.test.mjs` |
| Unit | Passkey challenge binding; assertion verification with recorded vectors; wrong challenge refused | `backend/tests/fabric/test_passkeys.py` |
| Unit | Share-target payload handling and taint | `backend/tests/fabric/test_share.py` |
| Integration | Push → notification action → signed decision → interrupt resolved (fake push service) | `backend/tests/fabric/test_push_decision.py` |
| Live | Android (relay) and iPhone (Tailscale): approve `tap` from the lock screen; approve `verify` with biometrics; Stop a run; share a link | evidence log checklist |

## 8. Acceptance criteria

- [ ] A `tap` approval completes from an Android notification without opening the app.
- [ ] A `verify` approval requires biometrics and fails if the action changes after the prompt was shown.
- [ ] Sharing a link from another app starts a PANINI thread.
- [ ] Stop on the phone stops a desktop task within 1 s.

## 9. Rollout and rollback

Ships with the fabric. Rollback: remote mode `off`; `lan` mode can still serve the v1 app until removal.

## 10. Open questions

- Allow `verify` approvals on the PC through Windows Hello as well? Recommendation: yes, as a later small item
  (WebAuthn in the shell's renderer with the same challenge binding).
