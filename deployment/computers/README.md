# PANINI sandbox computers (OpenBot)

PANINI builds the computer service and supervisor from CopilotKit/OpenBot revision
`b6932d31a8d6e7896c15139dfc27a6c6911deb27` (MIT), the revision OpenDots pins. BuildKit downloads the source from
that pinned Git context; nothing comes from a moving branch or a `latest` image.

The computer service is unchanged. The supervisor applies one fail-closed patch (`harden-supervisor.mjs`) to
child environment construction: each computer receives `HMAC-SHA256(COMPUTER_TOKEN, "panini-computer:" + id)`
instead of the master token. PANINI derives the same credential (`backend/hands/sandbox.py`). No supervisor
token, model key or master token reaches a computer. If the pinned upstream line changes, the patch refuses to
build; upgrades need a review of the API contracts, ownership and volume behaviour, and this patch together.

## Use

1. Start Docker Desktop.
2. PANINI Admin → Computers → **Set up the sandbox** (creates the tokens in PANINI's DPAPI secret store), or
   `.venv\Scripts\python.exe scripts/panini-sandbox.py setup`.
3. `.venv\Scripts\python.exe scripts/panini-sandbox.py build` (downloads the pinned OpenBot source and base
   images), then `... up`. `status` and `down` do what they say.
4. Turn on `PANINI_SANDBOX` (Admin → Settings → System → PANINI v2) and the computer's permission switches.

Stopping a computer keeps its files and browser profile (named volumes). After a master-token change, the
supervisor replaces owned containers on the next start and keeps their volumes. Never delete the volumes as
routine cleanup.

OpenBot's licence: [LICENSE.openbot](LICENSE.openbot).
