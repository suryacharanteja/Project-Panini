# Continuing projects

Projects group related background tasks under a durable title, goal and person-edited
context. They are available in PANINI Admin at **Projects**, and through both the
console and shell harness APIs:

- `GET/POST /api/harness/projects`
- `GET/PATCH /api/harness/projects/{id}`
- `POST /api/harness/tasks` with `project_id`

The shell uses the same paths under `/api/panini/harness`. Project state is stored
in the local harness SQLite database. Existing tasks remain unassigned.
The foreground agent can list and create projects, add a background task, and
change project status through its tools, so these actions can start from a typed
or spoken request. Background agents cannot invoke those management tools.

## Reviewed research plans

On a project's Admin page, **Draft plan** asks the configured model for one to
six ordered, read-only research steps. The draft is stored with a revision and
does not queue work. **Approve and start** checks that exact revision, then
queues the first step. After a step finishes with `verified_complete` and at
least one evidence ID, the background worker queues the next step. A failure,
unknown outcome, cancellation, or evidence-free completion puts the plan in
`needs_review`. Pausing the project prevents the next step from starting.

Plan advancement and task creation share one SQLite transaction. A restarted
worker reconciles approved plans, so a crash between steps does not duplicate
tasks. The plan is marked complete only after every step has verified evidence.
The project itself remains active for other work. Drafts are restricted to
research because background execution currently has Search access only.

Run `.venv\Scripts\python.exe scripts/panini-project-plan-smoke.py` for an
isolated live provider check. It verifies a draft remains non-executing and
that approval queues exactly the first step.

Project context is passed to a background run as task data. The worker still has
only read-only Search access. Pausing a project prevents queued tasks from being
claimed; it does not interrupt a task that is already running. Completing a
project also prevents new project tasks until it is set active again. The existing
lease recovery and review rules apply to project tasks without change.

Each task also receives up to five recent project results that were marked
`verified_complete` and have evidence IDs. Results are bounded to 1,000
characters each and explicitly labeled as data to recheck. Failed and
evidence-free outcomes are excluded from this shared history.

Projects do not yet coordinate concurrent workers or perform write-capable
background workflows. Those require explicit evaluation before PANINI can claim
Dot-like multi-project autonomy.
