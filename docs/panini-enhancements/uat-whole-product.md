# PANINI v2 — whole-product test for UAT

One command starts the whole PANINI runtime with every non-security v2 feature on, checks the parts working
together, then runs every end-to-end smoke in sequence.

```powershell
npm run product:test                         # integrated checks + all smokes (about 7 minutes)
npm run product:test -- --quick              # integrated checks only (about 1 minute)
npm run product:test -- --no-desktop         # skip the smokes that use the mouse and keyboard
npm run product:test -- --only integrated,phone,teach-notepad
npm run product:test -- --live               # also the live-provider checks (needs a funded model account)
```

Results: `artifacts/product-test/report.md` (and `report.json`), a screenshot of every Admin page in
`artifacts/product-test/pages/`, and one log per smoke (`smoke-<id>.log`).

## What it does

1. **Starts the real runtime**: gateway, backend, search and the native Windows broker, through the same
   `frontend/server/main.mjs` that `npm run panini` uses. It runs with all 23 non-security v2 flags on
   (`FLAGS` in `scripts/panini-product-test.mjs`).
2. **Checks the parts together**:
   - gateway and backend report the same instance;
   - the owner signs in with a one-time console ticket;
   - the flags read back exactly as set, with none blocked;
   - every console API (51 today) answers without a server error;
   - the backend lists real Windows windows through the broker and worker;
   - the receipt chain verifies;
   - all 33 Admin pages load directly (deep link or reload) and render with no page errors.
3. **Runs the existing smokes in sequence**:
   - the avatar shell (Electron, as an isolated second instance) and the unified runtime;
   - the offline scorecard, including the persona cases;
   - phone PWA and remote fabric, continuity, personas (Admin and avatar), channels, Spaces;
   - outbound calls, Android (fake transport), Teach (browser and Admin), MCP Admin, triggers Admin;
   - local model settings and offline speech;
   - the desktop smokes: broker/worker, cancellation, fine tools, real Notepad, Teach on Windows including drag,
     Teach in Notepad including Save As, and drawing in Paint.

## Isolation

- **Data, ports and secrets:** a throwaway data folder, its own ports (3261 / 8261 / 3291) and its own secrets.
  Your running PANINI, your data, pairings and accounts are untouched; the shell smoke uses its own Electron
  profile.
- **Keys:** model and speech keys (`OPENAI_API_KEY`, `AI_GATEWAY_API_KEY`, `ASSEMBLYAI_API_KEY`) are blanked for
  the runtime, so nothing spends money or reaches a provider.
- **Accounts:** channels and the phone line have no accounts in the throwaway data, so nothing contacts Telegram,
  Slack, WhatsApp, email or SIP.
- **Remote access:** set to relay mode with no relay address, so it reports `needs_setup` and opens no listener.
- **Desktop smokes:** they move the mouse and type on their own disposable windows (a WPF fixture, new Notepad
  documents, Paint). Leave the keyboard and mouse alone for a few minutes, or use `--no-desktop`.
- **Security:** the guardian and red-team enforcement stay off, as decided; they are not part of this test.

## Defects this test found and fixed (2026-10-06)

- Reloading or deep-linking `/spaces` (and `/spaces/<space>/pages/<page>`) in the console returned 404. The
  gateway's page list now includes them, and a gateway test covers it.
- The Keys & models page crashed when the local-model qualification had no `classes`. It is now guarded, and the
  local-mode smoke passes.
- A desktop request sent while the broker was starting, or still busy after a Stop, failed at once as "companion
  unavailable". The pipe now waits up to 5 s; a unit test covers it, and the broker cancellation and Teach smokes
  are stable.
- A delegate's run was recorded as PANINI's (`runs.persona_id`). It is now attributed to the specialist.

## Latest result

2026-10-06: **64 passed, 0 failed, 5 skipped** in 411 s.

The five skipped checks:

- live persona voices, the live research-to-writing team and live notebook citations: they need a funded model
  account, and OpenAI credit was exhausted;
- live sandbox computers: they need Docker Desktop with the sandbox images built;
- packaged MCP transports: they need a packaged build.

Physical Android, real SIP calls and audible voice playback remain separate manual UAT items.
