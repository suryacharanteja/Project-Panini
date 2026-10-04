# Telegram text channel development increment — 2026-10-04

V2-17 and the full LLD program remain in progress. This increment delivers Telegram text intake/replies,
shared durable channel infrastructure and Admin setup. Slack, WhatsApp, email, attachments, proactive
delivery, persona selection and automatic avatar handoff remain pending. No live bot qualification or
production promotion is claimed. Red-team qualification/enforcement stays disabled and deferred.

## Behavior

- Opt-in through PANINI_CHANNELS=telegram and an enabled account with numeric owner IDs.
- Bot tokens are write-only in the Windows user vault; configuration contains no token values.
- Explicit group allow-lists and human actors; duplicate/edited messages cannot create repeated work.
- Existing leased tasks, persistent conversation sessions, receipts and durable approval checkpoints.
- Every inner channel effect follows enforced channel policy even when local policy uses shadow mode.
- PC/paired-phone approvals only; replying "approve" in Telegram cannot resolve an interrupt.
- Account/group access removal stops queued and running work. Background hands remain least privilege.
- Queued results are cancelled before delivery when their originating owner's access is removed.
- Bounded plain-text chunks and per-thread/account queues; durable offset/reply bookkeeping.
- 429 retries honor Retry-After in order. Ambiguous sends stay unknown and are not automatically resent.
- Safe failure records include only error class and HTTP status; Telegram token URLs are redacted.

## Verification

Channel tests exercise real database transactions, the real agent/registry/policy/ledger, phone-authorized
resume, database reopen, in-flight cancellation and the real HTTP adapter against a fake provider.
The browser smoke runs real Admin account save/edit, secret clearing, owner intake through the durable
worker, reply dispatch and disable against an isolated fake Telegram service. Desktop/mobile screenshots
and build/test logs are retained in artifacts/channels. Live-provider behavior requires a configured bot
and owner account; it is not inferred from mocked HTTP or browser checks.

Focused channel, policy and harness verification: **135 passed**, including restart/resume, in-flight
revocation, queued-result revocation and credential-safe validation errors. Browser verification includes
desktop and 412px mobile layouts, no horizontal overflow, and same-page mobile navigation closing.

The repository check passed **843 backend tests**, plus CLI, gateway, Android PWA, Search, Admin,
shell and offline evaluation checks. Backend warnings include dependency deprecations and a Windows
Playwright pipe cleanup warning; CLI retains its existing skipped test. These are recorded in the log,
not counted as provider, physical-device or production qualification.

Existing physical Android/SIP, signing, clean-VM, multi-day and other LLD release requirements remain open.
