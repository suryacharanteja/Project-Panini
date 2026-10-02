# PANINI evals

Golden tasks that measure PANINI and gate each v2 phase
([LLD](../docs/panini-enhancements/lld/V2-01-eval-scorecard.md)).

```powershell
.venv\Scripts\python.exe scripts/panini-eval.py list
.venv\Scripts\python.exe scripts/panini-eval.py run --mode offline                  # scripted model, no network (runs in npm test)
.venv\Scripts\python.exe scripts/panini-eval.py run --mode live --engine v1         # real providers; costs a few cents
.venv\Scripts\python.exe scripts/panini-eval.py compare artifacts\evals\<run>.json --baseline artifacts\evals\baseline-v1-live-1.json --gate phase1
```

- Every case runs in its own temporary PANINI data folder; your memories, skills and pairings are untouched.
- Approvals are decided per case (`approvals: decline | approve | timeout`, default `decline`): risky actions
  are counted, never performed.
- Live cases that need hardware or accounts (`needs: [desktop]`, `chrome`, `docker`, `phone`, `android`) are
  **skipped, never passed**, unless you set `PANINI_EVAL_<NEED>=1` on a machine that has them.
- Results go to `artifacts/evals/` (git-ignored). Record live evidence in the v2 register's evidence log.

## Writing a case

One YAML file per case in `cases/<hand>/`. The id must start with the folder name.

```yaml
id: computer.offline-sum-csv
title: Sum a CSV (scripted)
modes: [offline]            # offline needs `script`; live needs real providers (`needs: [openai]`)
turn: "Add up the amounts in {work}\\numbers.csv."
files:                       # written into the case's private working folder ({work})
  numbers.csv: "item,amount\nrent,4200\n"
script:                      # offline only: the scripted model's turns (tool calls, then finish or text)
  - calls: [{name: read_file, arguments: {path: "{work}\\numbers.csv"}}]
  - calls: [{name: finish, arguments: {outcome: verified_complete, message: "The amounts add up to 4200."}}]
expect:
  outcome_in: [verified_complete]
  message_contains_any: ["4200"]
tags: [simple]               # "simple" cases form the latency gate
```

Expectations: `outcome_in`, `message_contains_all`, `message_contains_any`, `message_contains_newest_download`,
`message_contains_file_count`, `file_exists`, `file_absent`, `file_contains`, `approvals_requested_min`,
`approvals_requested_max`, `max_seconds`, `evidence_min`, `task_status_in`. `kind: background` runs the turn
as a durable background task.

Offline cases test PANINI's plumbing (tools, evidence rules, approvals, background policy) deterministically;
live cases measure real model quality. Phase gates use live runs.
