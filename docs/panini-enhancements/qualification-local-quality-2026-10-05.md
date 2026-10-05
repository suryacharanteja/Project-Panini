# V2-27 comparative local quality gate — 2026-10-05

The guard is implemented; no installed local model is qualified. V2-27 and the full LLD
program remain in progress. Red-team remains disabled and deferred.

## Guard

- `scripts/panini-eval.py run --mode local` uses the real loopback model and an isolated
  scorecard data folder. It does not replay the scripted offline cases or load a saved cloud
  credential. `live` runs supply the matching cloud baseline.
- `scripts/panini-local-qualify.py --cloud <live.json> --local <local.json> --output
  <quality.json>` checks the same engine and clean source commit, paired unskipped cases,
  a passing cloud safety subset, at least eight cases per task class, and local success at
  least 80% of the cloud rate. It records source scorecard hashes.
- Planner, desktop and agent fallback check their own class in the report for the exact model
  and source build. Missing, stale, malformed or underperforming reports disable that class.
  Admin shows each class's status. Candidate evaluation is scoped to the isolated scorecard.

## Real local diagnostic

The installed `qwen2.5:7b-instruct-q4_K_M` model scored **3/8 planner cases** and **1/1
safety case** (`artifacts/evals/20261005T083842Z-a6ff48-v1-local.json`). It missed the
17.5% of 2,340 case and claimed 411.5 instead of 409.5, among other misses. This run was
made while the implementation worktree was dirty, so it is diagnostic evidence and cannot
produce a source-matched qualification report. No matching cloud run was made; the 80%
relative threshold is therefore not evaluated. The runtime refuses its local task classes.

The earlier one-case local probe attempted a saved cloud credential; the evaluator now
prevents that import. A repeat one-case probe used the loopback model and recorded a blocked
result rather than a cloud API attempt.

## Remaining V2-27 acceptance

- Paired cloud/local scorecards for each task class, with a passing local model.
- Whole-process private-mode blocking-proxy proof.
- Real push-to-talk through local speech recognition into a verified Notepad draft.
- Audible local TTS and physical microphone qualification.

This is a local development checkpoint. It does not qualify V2-20 SIP/audio or physical
Android requirements, and no production deployment or signed release is available.
