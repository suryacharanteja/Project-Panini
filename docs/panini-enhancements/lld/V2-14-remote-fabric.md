# V2-14: Remote fabric (device keys, transports, blind relay, ingress, push)

| Field | Value |
| --- | --- |
| Phase / priority / size | Phase 3 · P0 · L (1.5–2 weeks) |
| Depends on | V2-05 (interrupts); V2-13 red-team gate before enabling |
| Flag | `PANINI_REMOTE_MODE` = `off` (default) · `lan` · `tailscale` · `relay` |
| Status | Not started |
| Decisions | Register D1 (transport), D10 (relay hosting) |

## 1. Outcome

Your phone (and later a watch or another browser) reaches PANINI from anywhere, securely, while the PC stays
the brain. Devices pair once by QR code and hold their own keys; every request and decision is
authenticated by the device; through the relay, traffic is end-to-end encrypted so the relay sees nothing;
provider webhooks (incoming calls, WhatsApp) arrive at the PC and are verified there; and PANINI can push
approvals to your lock screen.

## 2. Scope and non-goals

- In scope: device identity v2 and QR pairing with a short authentication string, envelope encryption,
  transports (LAN, Tailscale Serve, PANINI Relay), webhook ingress (Tailscale Funnel or relay) with per-provider
  verification on the PC, Web Push (VAPID), signed phone-app assets, the remote API surface, revocation, the
  relay implementation (Cloudflare Worker + Durable Object, and a Node reference relay), settings, tests.
- Not in scope: the phone app's screens (V2-15), passkeys (V2-15 registers them over this fabric), presence
  routing (V2-24), calls and channels (V2-16, V2-17 consume ingress).

## 3. Current state

`backend/harness/mobile.py`: opt-in listener on `0.0.0.0:3210`, private-address guard, `Sec-Fetch-Site`
check, CSP; 8-character one-time pairing code (5 minutes, 5 attempts per minute); device token as an
HttpOnly `SameSite=Strict` cookie, stored hashed (`mobile_devices`); API `/api/me`, `/api/inbox`,
`/api/ask` (creates a **background** task), `/api/reviews/{id}`, `/api/notices/{id}`. Plain HTTP, home
network only, no push. Open items recorded in `PANINI-AGENTIC-DESIGN.md` §10: HTTPS, push.

## 4. Harvest

| Source | What we take | Adaptation |
| --- | --- | --- |
| PANINI `mobile.py` | Pairing code rules, hashed tokens, revocation, CSP, private guard | Kept for `lan` mode; device keys added in all modes |
| PANINI extension pairing (protocol 4) | Secret never sent; mutual proof on every connection | Same principle for devices: keys never leave the device; every session is mutually authenticated |
| OpenDots `.env.example` / `src/server/app.ts` | `OWNER_TOKEN` required for any non-loopback binding; exact `APP_ORIGIN` | Remote modes refuse to start without at least one paired device and the V2-13 gate; exact origins per transport |
| OpenAI webhooks (Standard Webhooks headers `webhook-id`, `webhook-timestamp`, `webhook-signature`; verify and deduplicate) | Webhook verification | `ingress/openai.py` with the `openai` SDK's webhook verification, 5-minute tolerance, id dedupe |
| Meta WhatsApp Cloud API (`hub.mode/hub.verify_token/hub.challenge` GET; `X-Hub-Signature-256` HMAC-SHA256 of the raw body with the app secret) | Webhook verification | `ingress/whatsapp.py` |

## 5. Design

### 5.1 Components and files

| File | Purpose |
| --- | --- |
| `backend/fabric/devices.py` | Device registry v2, keys, pairing, SAS, revocation |
| `backend/fabric/envelope.py` | Session handshake, AES-GCM envelopes, sequence/replay checks |
| `backend/fabric/router.py` | Remote API surface (one router used by every transport) |
| `backend/fabric/transports/lan.py` | v1 listener, kept, now calling `router.py` |
| `backend/fabric/transports/tailscale.py` | Detection, `tailscale serve` / `funnel` configuration, identity headers |
| `backend/fabric/transports/relay_client.py` | Outbound WebSocket to the relay, reconnection, envelope I/O |
| `backend/fabric/ingress/__init__.py`, `openai.py`, `whatsapp.py`, `generic.py` | Webhook routing and verification |
| `backend/fabric/push.py` | Web Push with VAPID (`pywebpush`) |
| `backend/fabric/app_signing.py` | Signs the phone app's asset manifest with the PC key |
| `backend/harness/migrations/0009_fabric.sql` | `devices_v2`, `push_subscriptions`, `ingress_seen` |
| `panini-core/relay/worker/` | Cloudflare Worker + Durable Object (TypeScript) |
| `panini-core/relay/node/server.mjs` | Node reference relay (same protocol; tests and self-hosting) |
| Admin `src/pages/Devices.tsx` | Remote mode, transports, devices, QR pairing, webhooks, push test |

### 5.2 Device identity and pairing

Keys:

- PC: one long-term signing key (ECDSA P-256) and one long-term key-agreement key (ECDH P-256), generated on
  first enable, private keys in the DPAPI secrets store; `pc_id` = SHA-256 of the signing public key (base32,
  first 26 characters).
- Device: the same two key types generated in the browser with WebCrypto (`extractable: false`, stored in
  IndexedDB) or in the native app's keystore later. P-256 is chosen because WebCrypto supports it on every
  current browser, including iOS Safari.

QR payload (shown in Admin and on the avatar's "Pair phone" screen):

```text
https://<transport-origin>/pair#v=1&pc=<pc_id>&ks=<pc sign pub, b64url>&ke=<pc agree pub, b64url>&c=<8-char code>&exp=<unix>
```

The fragment never reaches a server. Flow:

```text
phone opens the URL (PWA) → generates device keys → POST /fabric/pair {code, name, device_sign_pub, device_agree_pub}
PC: code valid (single use, 5 min, 5 attempts/min as v1) → store device (pending) → respond {device_id,
    pc_sign_pub, signature over (device_id, device_pubs, pc_id)}
both screens show SAS = first 6 digits of SHA-256(pc_sign_pub ‖ device_sign_pub ‖ code), formatted 123 456
person confirms the match on the PC (one click) → device becomes active
```

The SAS step defeats a malicious relay or Wi-Fi attacker that swaps keys during pairing.

```sql
CREATE TABLE IF NOT EXISTS devices_v2(id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL,
  sign_pub TEXT NOT NULL, agree_pub TEXT NOT NULL, status TEXT NOT NULL,     -- pending | active | revoked
  created REAL NOT NULL, last_seen REAL, transports TEXT NOT NULL DEFAULT '[]', grants TEXT NOT NULL DEFAULT '{}');
```

v1 `mobile_devices` remain valid in `lan` mode until the person re-pairs; they cannot use relay or Tailscale.

### 5.3 Sessions and envelopes

Every connection (any transport) starts with a mutually authenticated handshake:

```text
device → PC : hello {device_id, eph_pub_d, ts, sig_d(eph_pub_d ‖ ts ‖ pc_id)}
PC → device : welcome {eph_pub_p, ts, sig_p(eph_pub_p ‖ eph_pub_d ‖ device_id)}
session key  = HKDF-SHA256(ECDH(eph_d, eph_p) ‖ ECDH(agree_d, agree_p), salt = pc_id ‖ device_id,
                           info = "panini-fabric-v1")     # ephemeral for forward secrecy + static for binding
```

Messages: `{v: 1, seq, kind: "req" | "res" | "evt", id, iv, ct}`; plaintext
`{method, path, headers, body}` or an event; AES-256-GCM with AAD `(v, seq, kind, id)`. Each direction keeps
a monotonic `seq`; gaps are allowed, repeats rejected; sessions expire after 12 hours or on revocation.

Transports carry envelopes the same way, so `router.py` sees authenticated plaintext requests with
`principal = Principal(kind="owner", surface="phone_app", device_id=…, verified=True)`. On `lan` and
`tailscale`, TLS (Tailscale) or the home network protects the outer channel; envelopes still provide device
authentication. On `relay`, envelopes are the only protection and the relay sees ciphertext only (I6).

### 5.4 Transports

| Mode | How | Origin for the phone app | Notes |
| --- | --- | --- | --- |
| `lan` | v1 listener (`0.0.0.0:3210`, private-address guard) | `http://<pc-lan-ip>:3210` | No push and no passkeys (they need HTTPS); kept for home use |
| `tailscale` | Listener binds `127.0.0.1:3210`; `tailscale serve` maps `https://<pc>.<tailnet>.ts.net` → `http://127.0.0.1:3210`; requests must carry `Tailscale-User-Login` equal to the configured owner login | `https://<pc>.<tailnet>.ts.net` | Valid TLS from Tailscale; works only for devices in your tailnet; enables push and passkeys |
| `relay` | `relay_client.py` holds `wss://<relay>/pc/<channel>`; devices use `wss://<relay>/d/<channel>`; the relay pairs sockets and forwards ciphertext | `https://<relay>/app/` (signed assets, §5.7) | Works anywhere; relay is blind |

Tailscale configuration is done by `tailscale.py` through the CLI (`C:\Program Files\Tailscale\tailscale.exe`
or `PATH`); it reads `tailscale status --json` for the MagicDNS name and verifies the serve config after
applying it. The exact CLI syntax is pinned to the tested Tailscale version and re-verified by a probe at
startup; on mismatch the mode reports `needs_setup` with manual steps instead of guessing.

### 5.5 Webhook ingress

Inbound calls (V2-16) and WhatsApp (V2-17) need a public HTTPS URL. Options in order of preference:

1. **Tailscale Funnel** on a dedicated path (`/hooks/<hook_id>/<secret>`): TLS ends on the PC, so even the
   provider's payload is never visible to a relay operator.
2. **Relay ingress**: `https://<relay>/hook/<channel>/<hook_id>/<secret>`. The relay forwards
   `{method, path, query, headers (allow-listed), body (base64), received_at}` to the PC inside the PC's
   WebSocket and returns the PC's response (or 503 when the PC is offline). The relay sees provider payloads
   in plaintext (unavoidable); it never sees PANINI secrets.

On the PC, `ingress/` verifies before anything else:

| Hook | Verification |
| --- | --- |
| `openai` (GPT-Live calls) | Standard Webhooks: HMAC-SHA256 over `id.timestamp.body` with the project's webhook secret (`whsec_…`, DPAPI), timestamp within 5 minutes, `webhook-id` not seen before (`ingress_seen`, 24 h) |
| `whatsapp` | GET: `hub.verify_token` equals the configured token → echo `hub.challenge`; POST: `X-Hub-Signature-256` equals `sha256=HMAC(app_secret, raw_body)` (constant-time) |
| `generic:<name>` (V2-25) | HMAC-SHA256 with a per-hook secret in `X-PANINI-Signature`, timestamp header, dedupe |

Unverified requests get `401` with no detail; the path secret only reduces noise, it is not trusted alone.

```sql
CREATE TABLE IF NOT EXISTS ingress_seen(hook TEXT NOT NULL, delivery_id TEXT NOT NULL, seen REAL NOT NULL,
  PRIMARY KEY(hook, delivery_id));
```

### 5.6 Web Push

- VAPID key pair generated on first enable (private key in DPAPI); public key served to the app.
- Subscriptions registered over the fabric: `push_subscriptions(device_id, endpoint, p256dh, auth, created)`.
- The PC sends pushes directly to the push service (`pywebpush`); payloads are encrypted to the device by Web
  Push (RFC 8291) and contain only `{type: "interrupt", id, title, level}` or `{type: "notice", id, title}`;
  details are fetched over the fabric after unlock.
- iOS requires the app installed to the Home Screen (iOS 16.4+) for push; the app explains this.
- `POST /api/harness/fabric/push-test` sends a test notification from Admin.

### 5.7 Signed phone-app assets (relay mode)

A compromised relay could serve modified JavaScript that steals device keys. Defence:

1. The PC builds the phone app, computes SHA-256 of every asset, and signs the manifest with its signing key
   (`app_signing.py`); the relay stores the assets and the signed manifest (uploaded over the PC's socket).
2. On first install (during pairing, after SAS confirmation) the service worker stores the PC signing key.
3. On every update, the service worker fetches the new manifest, verifies the signature with the stored PC key,
   checks each asset hash, and only then activates; otherwise it keeps the old version and shows "Update
   refused: not signed by your PC."

### 5.8 Remote API surface (`router.py`)

| Path | Purpose |
| --- | --- |
| `GET /status` | PANINI state, current runs, paused |
| `GET /threads`, `GET /threads/{id}`, `POST /threads/{id}/turns` | Continue conversations (foreground runs with principal `phone_app`, not v1's background tasks) |
| `POST /ask` | New thread + turn |
| `GET /interrupts`, `POST /interrupts/{id}/resolve` | Decisions carry `signature` = device signature over `(interrupt_id, nonce, action_digest, status, payload_digest)`; V2-05 stores it in `decided_by` |
| `GET /live/{hand}/{target}/stream` | Live frames at ≤ 1 fps (V2-12 grants apply) |
| `POST /runs/{id}/stop`, `POST /pause` | I10: every remote surface can stop what it can see |
| `GET /notices`, `POST /notices/{id}` | Inbox |
| `POST /push/subscribe`, `DELETE /push/subscribe` | Push |
| `POST /share` | Share target (V2-15) |
| `GET /devices/self`, `DELETE /devices/self` | Forget this phone |

Rate limits: 30 requests/minute per device for writes, 120 for reads; pairing as v1.

### 5.9 Relay

Protocol (both implementations):

| Endpoint | Who | Behaviour |
| --- | --- | --- |
| `POST /channels` | PC (once) | Creates a channel; returns `{channel_id, pc_secret}`; `pc_secret` stored in DPAPI |
| `GET /pc/{channel}` (WS) | PC | Auth: HMAC challenge with `pc_secret`; one PC socket per channel |
| `GET /d/{channel}` (WS) | devices | No relay-level auth beyond the channel id and rate limits; the relay assigns a connection id and forwards frames `{conn, data}` to the PC and back |
| `POST|GET /hook/{channel}/{hook}/{secret}` | providers | Forwarded to the PC as described in §5.5 |
| `GET /app/*` | browsers | Static phone-app assets + signed manifest |

Limits: 64 device sockets per channel, 1 MiB per frame, 60 frames/s per socket; nothing is stored except the
app assets and the channel record. Cloudflare implementation: one Durable Object per channel using the
WebSocket Hibernation API; deploy with `wrangler`. The Node reference relay implements the same protocol in
one file and runs in tests.

### 5.10 Security summary

| Risk | Control |
| --- | --- |
| Stolen phone | Revoke from Admin (immediate: sessions closed, keys refused); `verify`-level actions need the passkey (V2-15) |
| Relay operator | Ciphertext only; signed app assets; SAS at pairing |
| Replay | Sequence numbers, session expiry, decision signatures include the interrupt nonce |
| Webhook forgery | Provider signature verified on the PC; dedupe |
| Accidental exposure | Remote modes refuse to start without the V2-13 red-team gate and at least one active device |

## 6. Implementation steps

1. Device keys, pairing with SAS, `devices_v2`; migrate `lan` mode to the shared router.
2. Envelope handshake and messages (Python + a small JS module shared with the phone app); test vectors.
3. Tailscale transport (detect, configure, verify, identity header).
4. Node reference relay + `relay_client.py`; then the Cloudflare Worker with the same tests.
5. Ingress (OpenAI, WhatsApp, generic) with verification and dedupe; Funnel option.
6. Web Push and subscription management.
7. Signed app assets and the service-worker verification (with V2-15).
8. Admin Devices page; gating on V2-13; revocation flows.

## 7. Tests

| Level | Test | File |
| --- | --- | --- |
| Unit | Pairing: code rules, SAS computation, pending → active only after confirmation | `backend/tests/fabric/test_pairing.py` |
| Unit | Envelope test vectors (Python ↔ JS), replay rejection, session expiry | `backend/tests/fabric/test_envelope.py`, `panini-core/apps/phone/test/envelope.test.mjs` |
| Unit | Webhook verification: valid, bad signature, stale timestamp, duplicate id, WhatsApp challenge | `backend/tests/fabric/test_ingress.py` |
| Unit | Decision signatures stored and verified; revoked device refused | `backend/tests/fabric/test_decisions.py` |
| Invariant | I6: relay log of a full session contains no plaintext request paths or bodies; I10: stop works over each transport | `backend/tests/fabric/test_invariants.py` |
| Integration | Node relay end to end: pair, ask, resolve an interrupt, stream a live frame | `backend/tests/fabric/test_relay_e2e.py` |
| Live | Phone on mobile data via relay: approve a push notification; Tailscale mode on the same phone | manual checklist in the evidence log |

## 8. Acceptance criteria

- [ ] A phone pairs by QR with SAS confirmation and works over LAN, Tailscale and relay.
- [ ] Relay sees only ciphertext (I6 test); revocation is immediate.
- [ ] A verified OpenAI test webhook and a WhatsApp challenge reach the PC through Funnel or the relay.
- [ ] Push notifications arrive on Android and on an installed iOS PWA.

## 9. Rollout and rollback

`off` by default; Admin's Remote access page walks through red-team gate → pairing → transport choice.
Rollback: `off` closes listeners and the relay socket; devices stay registered.

## 10. Open questions

- Queue device requests at the relay while the PC sleeps? Recommendation: no (fail fast with "Your PC is
  offline"); pushes still work because the PC sends them when awake.
