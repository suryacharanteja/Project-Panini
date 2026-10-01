# PANINI capability benchmark

The 20-case live suite in `scripts/panini-capability-eval.py` measures everyday
tasks through the real coordinator and providers. It uses a fresh PANINI data
directory and creates file fixtures inside that directory. It declines approval
requests and records them; a refused action is not counted as a completed effect.
The suite can still open apps and browse the web, and it consumes provider credits.

Run it on the same machine and provider configuration before and after a change:

```powershell
.venv\Scripts\python.exe scripts/panini-capability-eval.py
Copy-Item artifacts/capability-eval.json artifacts/capability-baseline.json
# Apply the candidate change, then run the suite again.
.venv\Scripts\python.exe scripts/panini-capability-eval.py
.venv\Scripts\python.exe scripts/panini-eval-report.py artifacts/capability-eval.json --baseline artifacts/capability-baseline.json --output artifacts/capability-comparison.json
```

The report exits nonzero if a previously passed case fails or the case set
changes. It also reports solved cases, evidence-backed solves, median time,
total tokens and cost, and approval requests. These metrics do not establish
equivalence to another product: a head-to-head comparison needs the same task
corpus, environment, permissions, and independent outcome checks for both.

The live suite's `passed` checks differ by case. Some test the task outcome,
others inspect a file or answer text. `evidence_count` records PANINI's evidence
IDs separately so a text match is not mistaken for verified completion. The
comparison gate currently protects solve count and case identity; time, cost,
approval rate and evidence quality require judgment until budgets are set.

## Elapsed-time run

`scripts/panini-longrun-eval.py` runs the same live cases on a schedule and
persists each sample under `artifacts/longrun-eval/`. It resumes from
`state.json` after process restarts and compares later samples with the first.
For a scheduler, `--once` runs one due sample and exits; invoke it daily with
the same options. Three runs separated by 24 hours are the default. This is an
elapsed-time reliability check, while lease and project-plan crash recovery are
covered by accelerated fault-injection tests. Neither substitutes for a full
head-to-head evaluation with Dot.
