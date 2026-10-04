# Android hands development checkpoint — 2026-10-04

## Delivered implementation

V2-21 now has an opt-in Android hand connected to the real registry and harness, authenticated Admin setup,
wireless pairing and connection, USB discovery, explicit phone selection and driver-installation consent.
The hand provides thirteen tools: devices, snapshot, screenshot, tap, longtap, fill, press, swipe, scroll,
launch, open_url, verify_text and bounded wait_for. Models cannot invoke arbitrary ADB shell commands.

UIAutomator snapshots use generation-scoped refs. An effect refreshes the tree before review and again
before dispatch, checking the package, actual label, field identity, bounds, focus and unique selector.
Effects invalidate refs. Typed password/security-code fields are refused, credential screens cannot produce
screenshots/live frames, all app packages except fixed launcher/keyboard system UI require an explicit
owner allowance (package names alone cannot reliably identify every bank), and observations are
quarantined as phone content. Owner approvals are still required by policy; the app allowance grants none.

Ledger logging is mandatory for this hand. Its policy hook enforces cancellation even in general shadow
mode, and records the effect of the grounded control (for example Send becomes communicate). Failed or
interrupted effects are reported as unknown and never automatically retried. The existing live hub polls at
one frame per second while watched. Admin and the paired phone Home expose Stop; takeover/handback uses the
existing control state machine. PWA cache version is v11.

The pinned Playwright Android driver APKs are downloaded from the official Playwright CDN at build time
and included in the unsigned installer. Source development uses the pinned CLI's `install android`
command. Android SDK Platform Tools remain an explicit setup dependency; existing SDK installs are found
on PATH, under LOCALAPPDATA/Android/Sdk, or through ANDROID_HOME/ANDROID_SDK_ROOT. Pairing codes go through
stdin and are not stored in the configuration or process arguments. When tools are missing, Admin offers
the official Google download with a checked filename/size, an Android SDK license link, explicit consent
and extraction instructions. The download source follows the [official Platform Tools page](https://developer.android.com/tools/releases/platform-tools).

V2-26 has its shared snapshot renderer/parser and role map in this checkpoint. Fine Windows tools, broker
tree snapshots, sandbox normalization and the desktop comparison gate are not delivered by this checkpoint.

## Verification

The focused test suite exercises the hand through the real registry, ledger and policy hooks with a fake
phone transport. It covers password/secret scrubbing, malformed XML, grammar round trips, target changes,
ambiguous selectors, approval denial, sensitive-app allowance, setup changes, pause during an effect,
takeover, bounded waits, truthful verification and unknown-effect reporting.

The browser smoke uses actual Admin and phone Home modules with a disposable database and fake phone:
saved setup, responsive desktop/mobile layout, pairing-code clearing, an effect through the registry,
live frames and the phone's Stop control. Its phone request adapter uses authenticated loopback routes;
it does not claim physical Android, WebCrypto pairing or an end-to-end remote-network result.

Exact counts, source commit, package hashes and runtime-integrity results are retained in
`artifacts/android-hands/`. `release-check.log` must pass before release packaging. The installer remains
an unsigned local development release.

## Open requirements

- Physical Android pairing, real tap/fill/swipe and approved Send goldens, bank refusal and live-view testing.
- Android Chrome/WebView qualification on a real device.
- Screenshot-only vision fallback when XML is unavailable. This checkpoint refuses unverified app state,
  rather than exporting an unknown sensitive screen.
- Persistent Android debugging notification and phone Home active-control notice tested on physical hardware.
- The remainder of V2-22–27 and earlier cross-provider/production acceptance evidence remain open.

Red-team work stays disabled and deferred. No passing red-team, real phone call, audible playback,
physical Android, full LLD completion or production qualification is claimed.
