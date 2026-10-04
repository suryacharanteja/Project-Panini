# PANINI Android PWA

Red-team qualification and enforcement are **disabled and deferred** by owner. Pairing, signed decisions and action permissions remain active. Production transport and physical Android qualification remain open.

## Implemented

- Separate Home, Inbox, Ask, Work, Live, Settings and Pair screen modules.
- IndexedDB persistence of non-extractable device keys and Ask drafts.
- Encrypted pairing/session client, serialized requests, bounded timeouts, and no automatic replay of effects after an ambiguous reply.
- Restore-before-decide approval signing and stale-card checks.
- WebAuthn passkey enrollment/removal and action-bound `verify` decisions. The PC validates origin,
  RP ID, challenge, signature, user verification and counters; challenges expire and are single-use.
- Notebook creation/editing, local drafts, explicit conflict review, trash/restore, Markdown/PDF export
  and saving verified task results or finalized call receipts through the encrypted fabric.
- Android install prompt, app manifest, share target, offline app shell and explicit notification permission.
- Work shows recent background-task history, results and activity, with an exact-task Stop action. This is task history, not a full chat transcript.
- Revocation clears the local pairing; live view stops polling while hidden and reports connection failures.

## Developer checks

From the repository root:

```powershell
node --test panini-core/apps/phone/test/*.test.mjs
node scripts/panini-phone-smoke.mjs
.venv/Scripts/python.exe scripts/panini-phone-dev.py
```

The smoke starts isolated loopback servers on ports 3221/3222 and a throwaway database. It exercises actual fabric encryption, PC confirmation, signed approval, Ask, Live, sharing, offline recovery and revocation in mobile-sized Chromium. It does not perform real email actions. The default manual preview uses ports 3211/3212; alternate ports are supported.

## Test on Android

With Android USB debugging and Google's platform-tools installed:

```powershell
adb reverse tcp:3211 tcp:3211
```

Open the preview's pairing link on the phone, using `http://localhost:3211` through the USB tunnel. Confirm the matching digits on the PC. Install from Chrome's app menu or PANINI's install button when offered. The control server remains PC-local. Do not bind this developer preview to a public or LAN interface.

For product use, the PWA needs a trusted HTTPS deployment/transport. An ordinary HTTP LAN address does not provide the browser security context required by these APIs.

Remote settings includes **Passkey app origin**. Set the exact HTTPS origin used by the phone (scheme,
DNS hostname and port, without an app path). It defaults to the active transport origin. Changing origin
requires new enrollment. IP-address RP IDs are refused. HTTP localhost is supported only for local
development. Passkeys use the platform screen lock; biometric-only enforcement is not claimed.

## Qualification remaining

Physical Android installation, lock-screen push, share-sheet routing, background lifecycle and device
removal need device evidence. Full conversation transcripts, photo/voice input and an APK remain open.
`verify` decisions are implemented; `local` approvals stay PC-only. WebAuthn was exercised with a virtual
Chromium authenticator through the real fabric. Android SDK is installed; no connected device was found.

An APK wrapper can follow the PWA using Android Trusted Web Activity once a stable owned HTTPS origin and signing identity exist; it needs Digital Asset Links verification. See Google's [Android guide](https://developer.android.com/develop/ui/views/layout/webapps/guide-trusted-web-activities-version2).
