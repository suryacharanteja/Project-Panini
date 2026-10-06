# V2-17: Messaging channels (Slack, Telegram, WhatsApp, email)

| Field | Value |
| --- | --- |
| Phase / priority / size | Phase 3 · P1 · L (1.5 weeks; channels can ship one at a time) |
| Depends on | V2-06 (run engine), V2-13 (guardian gate), V2-14 (WhatsApp ingress only) |
| Flag | `PANINI_CHANNELS` = comma list of `slack`, `telegram`, `whatsapp`, `email` (default empty) |
| Status | In progress: Telegram, Slack, WhatsApp and email built and tested with fakes; live accounts pending |
| Decision | Register D5 (WhatsApp Cloud API only) |

## 1. Outcome

You can message PANINI where you already are: mention it in Slack, DM it on Telegram, text it on WhatsApp, or
email it, and continue the same conversation on the avatar or phone. PANINI answers in the thread, does the
work on your PC, and sends approvals to your phone. Only you (and contacts you allow) can reach it, and it
never echoes provider errors.

## 2. Scope and non-goals

- In scope: a channel interface and manager; Slack (Socket Mode), Telegram (long polling), WhatsApp (Cloud
  API through ingress), email (IMAP IDLE + SMTP for a dedicated mailbox); identity allow-lists; thread
  bindings; formatting; attachments; proactive notices; rate limits; safe failures; Admin setup.
- Not in scope: hosted CopilotKit Channels (ADR-001), unofficial WhatsApp automation (terms of service),
  in-channel approvals (I4), group-chat moderation features.

## 3. Current state

No messaging channels. Remote access is the LAN phone app; agents can reach PANINI over A2A.

## 4. Harvest

| Source | What we take | Adaptation |
| --- | --- | --- |
| OpenDots `src/server/slack-channel.ts` | `slackIdentity`: provider must be Slack, tenant id equals the configured team, actor is human, actor id in the explicit user allow-list; `eligible()`: platform, owner, human actor, allow-listed, operation `created` (edits and deletes ignored); mention → subscribe to the thread → run; follow-ups only in subscribed threads; paused → notice without running or subscribing; on failure post a generic reply and report both failures; **`safeFailure`**: only error class names and HTTP status codes are logged or posted, never provider messages | Same rules for every channel (§5.3) |
| OpenDots `tests/slack-channel.test.ts` (7 cases) | Behaviours | Ported for Slack and generalized for others |
| OpenDots `dot-agent.ts` channel mode | Threads created for new channel conversations; channel errors replaced by a generic message | Thread bindings; generic errors |
| OpenTag (referenced by OpenDots) | Explicit workspace/user allow-lists and a selected specialist per channel | Channel default persona (V2-18) |

## 5. Design

### 5.1 Components and files

| File | Purpose |
| --- | --- |
| `backend/channels/base.py` | `Channel` protocol, `InboundMessage`, `OutboundMessage`, `ChannelManager` |
| `backend/channels/identity.py` | Allow-lists and principal mapping |
| `backend/channels/format.py` | Markdown → Slack mrkdwn, Telegram HTML, WhatsApp text, email (text + HTML) |
| `backend/channels/slack.py` | Slack Bolt for Python, Socket Mode |
| `backend/channels/telegram.py` | Bot API over `httpx` (`getUpdates` long polling, `sendMessage`, `sendChatAction`, `getFile`) |
| `backend/channels/whatsapp.py` | Cloud API: webhook handler (V2-14 ingress) and Graph API sender |
| `backend/channels/email.py` | IMAP IDLE (in a thread with `imaplib`) and SMTP (`smtplib`) for a dedicated mailbox |
| `backend/harness/migrations/0012_channels.sql` | `channel_accounts`, `channel_messages` (dedupe) |
| Admin `src/pages/Channels.tsx` | Setup per channel, allow-lists, default persona, test message |

### 5.2 Interfaces

```python
@dataclass(frozen=True)
class InboundMessage:
    channel: str                 # "slack" | "telegram" | "whatsapp" | "email"
    account: str                 # configured account id
    thread_key: str              # slack "T:C:thread_ts" · telegram "chat[:topic]" · whatsapp "wa_id" · email root Message-ID
    message_id: str              # provider id (dedupe)
    sender_id: str               # provider user id / phone / address
    sender_name: str
    text: str
    attachments: tuple[str, ...] # artifact ids (tainted)
    kind: Literal["mention", "dm", "reply", "edit", "delete"]
    received_at: float

class Channel(Protocol):
    name: str
    async def start(self, deliver: Callable[[InboundMessage], Awaitable[None]]) -> None
    async def stop(self) -> None
    async def send(self, thread_key: str, message: "OutboundMessage") -> str     # provider message id
    async def typing(self, thread_key: str) -> None
    async def readiness(self) -> Readiness
```

`ChannelManager.deliver(msg)`:

1. Dedupe on `(channel, message_id)` (`channel_messages`, 7 days).
2. Ignore `edit`/`delete` and non-human senders (OpenDots `eligible`).
3. Identity (§5.3) → principal or ignore.
4. If PANINI is paused → reply "PANINI is paused. Resume it on your PC or phone." (no run).
5. Thread binding `(surface=channel, external_id=thread_key)` → thread (V2-06).
6. `typing()`; `RunEngine.run_turn(principal, thread, text, attachments, persona=channel default)`.
7. Reply in the same thread with the run's message (formatted, chunked); results with URLs as links; long
   results become a Space page link (V2-19) when enabled.
8. On failure: generic reply "I couldn't complete that. Check PANINI and try again." + `safeFailure` log.

### 5.3 Identity

| Channel | Owner rule | Contacts |
| --- | --- | --- |
| Slack | `team_id` equals the configured team **and** user id in `SLACK_OWNER_IDS`, actor human | Optional allow-listed user ids with a persona and observe-only grants |
| Telegram | `from.id` in `TELEGRAM_OWNER_IDS` (numeric ids, not usernames) and chat type `private` (or an allow-listed group id) | Optional |
| WhatsApp | `from` (E.164) in `WHATSAPP_OWNER_NUMBERS` | Optional |
| Email | `From` address in the allow-list **and** the receiving server's `Authentication-Results` shows `dkim=pass` or `spf=pass` aligned with the From domain | Optional |

Owner principal: `Principal(kind="owner", surface=<channel>, verified=True)` → policy tier **channel** (V2-03:
effects need approval on the phone or PC). Contacts: `Principal(kind="channel_user")` → tier **external**
(no owner data, no effects). Everyone else is ignored; the first message from an unknown sender in a DM gets a
single "I only respond to my owner." per sender per day.

### 5.4 Approvals and effects

- The run's reply in the same thread is part of the conversation, not a tool effect (like the avatar
  speaking).
- Model-initiated sends to other people or places (`channel_send` tool, email to a new recipient, posting in
  another Slack channel) are `communicate` effects with interrupts.
- Interrupts are **never** resolved from channel text (I4). The reply says "Approval needed: check PANINI on
  your phone." with a deep link to the phone app's card when the fabric is on.

### 5.5 Channel specifics

| Channel | Transport | Threading | Limits and notes |
| --- | --- | --- | --- |
| Slack | Socket Mode with an app-level token (`xapp-`) and a bot token (`xoxb-`); events `app_mention`, `message.im`; no public URL | Mention → reply in `thread_ts`; follow-ups only in subscribed threads (OpenDots) | ~1 message/second per channel; files via `files_upload_v2` (share_out when not the owner's DM) |
| Telegram | `getUpdates?timeout=50&offset=` long polling; no public URL | One thread per private chat; forum topics as `chat:topic` | 30 messages/second global; voice notes → STT (AssemblyAI, existing) when enabled |
| WhatsApp | Webhook through V2-14 ingress (GET challenge + `X-Hub-Signature-256`), send via `POST /{phone_number_id}/messages` | One thread per `wa_id` | Business-initiated messages outside the 24-hour service window need approved templates; proactive notices use a template `panini_notice` |
| Email | IMAP IDLE on a dedicated mailbox (OAuth2/XOAUTH2 or app password), SMTP submission | Thread by `References`/`In-Reply-To` root | Replies quote minimal context; attachments as artifacts (tainted); never auto-reply to mailing lists (`List-Id`) or auto-generated mail (`Auto-Submitted`) |

### 5.6 Proactive messages

Notices (results, watch alerts, digests from V2-25) can be delivered to a preferred channel set in
Admin; delivery choice moves to presence routing in V2-24. Proactive sends to the owner's own DM/chat are not
`communicate` effects (the owner is the recipient); anything else is.

### 5.7 Secrets and configuration

Tokens (`SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`, `TELEGRAM_BOT_TOKEN`, `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_APP_SECRET`,
`WHATSAPP_VERIFY_TOKEN`, email credentials) are stored with the DPAPI secrets store; allow-lists and the
default persona per channel live in `channel_accounts`.

```sql
CREATE TABLE IF NOT EXISTS channel_accounts(id TEXT PRIMARY KEY, channel TEXT NOT NULL, label TEXT NOT NULL,
  config TEXT NOT NULL, persona_id TEXT NOT NULL DEFAULT 'panini', enabled INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS channel_messages(channel TEXT NOT NULL, message_id TEXT NOT NULL, seen REAL NOT NULL,
  PRIMARY KEY(channel, message_id));
```

### 5.8 Failure modes

| Failure | Behaviour |
| --- | --- |
| Socket Mode or long-poll disconnect | Exponential backoff 1–60 s; readiness `degraded` |
| Run fails | Generic reply; `safeFailure` log; if the reply also fails, both failures logged (OpenDots) |
| Subscription bookkeeping fails (Slack) | Still answer the mention, but never run on unreadable subscription state (OpenDots) |
| Rate limited | Respect `Retry-After`; queue at most 20 outbound messages per thread |

## 6. Implementation steps

1. `base.py`, identity, formatting, dedupe table; `ChannelManager` with a fake channel and tests.
2. Telegram (simplest: no public URL, no SDK); live test with a bot.
3. Slack Socket Mode with Bolt; ported OpenDots tests; live test in a test workspace.
4. Email (IMAP IDLE + SMTP) with authentication-results checks.
5. WhatsApp through ingress; template for notices.
6. Admin Channels page; preferred channel for notices.

## 7. Tests

| Level | Test | File |
| --- | --- | --- |
| Ported from OpenDots | Explicit tenant and user allow-lists for owner identity; answers an allowed mention once, then only subscribed follow-ups; ignores non-human actors and changed/deleted messages; absent operation means created; reports pause without subscribing or running; isolates a failed run after a safe reply and surfaces both failures; answers mentions when subscribing fails but never runs on unreadable subscription state; surfaces a failed pause notice safely | `backend/tests/channels/test_slack.py` |
| Unit | Identity rules per channel; unknown sender rate-limited notice | `backend/tests/channels/test_identity.py` |
| Unit | Formatting and chunking per channel | `backend/tests/channels/test_format.py` |
| Unit | WhatsApp challenge and signature (with V2-14); email auth-results parsing; auto-reply suppression | `backend/tests/channels/test_whatsapp.py`, `test_email.py` |
| Invariant | I4: an approval interrupt cannot be resolved by any channel message | `backend/tests/channels/test_no_channel_approval.py` |
| Eval | Red-team channel injection cases (V2-13) | `evals/redteam/` |
| Live | Telegram and Slack: ask, start a task, approve on the phone, result in the thread | evidence log |

## 8. Acceptance criteria

- [ ] Telegram and Slack live with allow-lists; WhatsApp and email pass their integration tests (live when
      accounts exist).
- [x] Ported OpenDots Slack tests green (`backend/tests/channels/test_providers.py`, 2026-10-06).
- [x] Provider errors never appear in replies or logs (safe-failure tests for Telegram and Slack, 2026-10-06).
- [x] A conversation started in Telegram (or any owner channel) continues on the avatar (thread binding, 2026-10-06).

## 9. Rollout and rollback

Enable channels one by one in Admin. The owner has disabled/deferred the V2-13 red-team gate;
this increment remains a development release with action approvals enforced. Rollback: remove the channel from
`PANINI_CHANNELS`; bindings remain.

## 10. Open questions

- Allow trusted contacts (family) to ask PANINI for shared information (for example a family calendar)?
  Recommendation: later, through a dedicated persona with read-only grants (V2-18).

## 11. Telegram text increment — 2026-10-04

Migration 0025 adds account configuration, inbound dedupe, channel jobs, polling offsets and a durable
reply outbox. Bot credentials use the existing DPAPI vault; responses expose only a configured boolean.
The Bot API adapter long-polls the official API, persists offsets only after durable intake, and replies
as bounded plain text. Numeric owner/group allow-lists, human-only intake, paused behavior, per-account
task limits and bounded reply queues are enforced. Edited messages do not start work.

Each accepted message creates an existing leased task bound to a persistent channel conversation.
The channel principal is preserved in run records and receipts. Channel policy is enforced even when
local policy is shadow/off; effects park on interrupts on the PC/paired phone. Account/group revocation
cancels queued work and stops in-flight hands. A reopened database resumes a parked memory write after
phone approval without repeating it. Channel text cannot resolve approvals.

Replies are deduplicated durably. Explicit 429 rejections respect Retry-After and preserve chunk order;
timeouts/crashes during sending become unknown delivery and are not blindly resent. Provider messages
and bot credentials are redacted from logs. Admin Build → Channels configures and disables accounts.

The browser check uses real Admin APIs, a real durable worker and a fake Telegram HTTP provider in an
isolated loopback preview. It does not qualify a live bot. Telegram attachment/voice input, proactive
notices, automatic avatar handoff, persona selection, Slack Socket Mode, WhatsApp and email remain open.
Remote desktop/code hands are not enabled by this increment. Red-team work remains disabled/deferred.


## 12. Slack, WhatsApp and email — 2026-10-06

All four channels share the manager, dedupe, outbox, jobs, policy and guard. No new SDKs.

- **Accounts** (`accounts.py`): per-channel validation of owner IDs, groups, settings and write-only secrets
  (Slack bot + app tokens and workspace ID; WhatsApp access token, app secret, verify token, phone number ID;
  email mailbox settings and password). An account cannot change channel.
- **Identity** (`identity.py`): Telegram unchanged; Slack needs the exact workspace and member IDs, and a DM or an
  allow-listed channel; WhatsApp needs the owner's number in a 1:1 thread; email needs an allow-listed address
  **and** aligned DKIM or SPF in the receiving server's Authentication-Results.
- **Slack** (`slack.py`): Socket Mode over a plain WebSocket (`apps.connections.open`), every envelope acknowledged
  before work, replies in thread via `chat.postMessage`. OpenDots behaviour ported: mentions subscribe their
  thread (not while paused; a failed subscribe still answers), follow-ups only in subscribed threads and never on
  unreadable state, bots/apps/edits/deletes ignored.
- **WhatsApp** (`whatsapp.py`): inbound through V2-14 ingress (challenge and X-Hub-Signature-256 verified on the
  PC), one thread per number, replies through the Graph API. It needs remote access for its public webhook.
- **Email** (`mail.py`): IMAP polling with the last UID stored after durable delivery, threads by
  References/In-Reply-To root, quoted history removed, SMTP replies in thread marked `Auto-Submitted`; mailing
  lists, auto-generated mail and PANINI's own address are never answered.
- **Avatar handoff** (`brain/threads.py`): the avatar's continue-recent now includes owner channel threads.
- **Admin** → Channels: one page for all four channels.

Tests: `backend/tests/channels/test_providers.py` (14), on top of the existing 26 Telegram tests. Covered:
- the seven OpenDots Slack cases;
- Socket Mode acks before work;
- WhatsApp through real ingress (forged signature refused);
- email authentication alignment, auto-mail suppression, threading and UID bookkeeping;
- channel text can never resolve an approval (I4);
- write-only routes;
- avatar handoff.

Not done: live qualification with a real Telegram bot, Slack workspace, WhatsApp number and mailbox (they need
your accounts and tokens); channel attachments.

### Proactive notices and long results — 2026-10-06

- **Proactive notices:** V2-24 presence routing already delivers notices to a preferred channel. It now accepts
  any owner's private conversation, not only chats whose thread key equals the sender: Telegram and WhatsApp
  1:1 chats, Slack direct messages, and email threads. Groups and Slack channel threads are never notice
  destinations (`identity.private_conversation`).
- **Long results (with V2-19):** a verified channel result longer than 3,000 characters is saved as a page in
  the owner's default Space (when Spaces are on and a default Space is set). The reply is a short summary
  naming the page. Retries reuse the same page; without a Space the full text is sent in chunks as before.
