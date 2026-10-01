# PANINI product design standards

This is PANINI's implementation guide for a persistent, supervised assistant. It uses public product patterns from [Dots](https://openai.com/index/introducing-dots/), [the Codex app](https://openai.com/index/introducing-the-codex-app/), and [ChatGPT Projects](https://openai.com/academy/projects/) as references. Those pages describe product behavior, not reusable OpenAI source code or an official UI kit. PANINI keeps its own visual identity.

## Primary experience

1. **Goal first.** Home leads with continuing projects and decisions. A decorative identity moment is secondary and must not hide work below the fold.
2. **One workspace per goal.** A project groups its goal, context, proposed plan, execution state, tasks and verified results. The current project is selected on entry.
3. **Show the supervision boundary.** A drafted plan names the exact revision and all steps before approval. A running plan shows the current task and verified progress. A failed or evidence-free step stops in `needs_review`.
4. **Make state understandable without color.** Text labels accompany every status. Success requires evidence; queued and merely completed are distinct.
5. **Keep control visible.** Pause and resume are available in the workspace. Connector access is granted per server for read-only background use. Activity and receipts let the person inspect what happened.
6. **Match the promise to the implementation.** Current autonomous plans are sequential read-only web research. Multi-app effects, cloud continuity, and head-to-head Dot quality are future qualification work, not interface claims.

## Interaction and visual rules

- Use PANINI's existing Obsidian, copper, amber, gold and slate tokens in `panini-core/apps/admin/src/styles.css`. Copper marks a primary action or selected item; amber marks active execution or needed attention; gold marks verified success; danger marks failure.
- Use `Page`, `Card`, `Button`, `Badge`, `Field`, `Empty`, `StateDot`, and `useAction` from `panini-core/apps/admin/src/components/ui.tsx`. Keep loading, empty, error and success states in each data view.
- Label controls with the action and object, such as “Approve revision 2 and start”. Do not use generic “Continue” for a consequential transition.
- Make progress numerical and accessible. A plan progress bar counts only steps with `verified_complete` and evidence.
- Keep primary work visible at laptop widths and let the layout stack on narrow screens. Do not depend on hover to reveal essential information.
- Respect keyboard focus and reduced-motion preferences. Dynamic updates must not silently switch the selected project or overwrite its data with a response from a previous selection.

## Repository boundaries

| Area | Responsibility |
| --- | --- |
| `panini-core/apps/admin/src/pages/Projects.tsx` | Goal workspace and plan supervision |
| `panini-core/apps/admin/src/pages/Home.tsx` | Entry point, continuing work and health |
| `panini-core/apps/admin/src/components/` | Shared interface primitives and navigation |
| `panini-core/apps/admin/src/api.ts` | Authenticated API client and resource lifecycle |
| `backend/harness/projects.py` | Durable goal and context records |
| `backend/harness/project_planner.py` | Plan revisions, approval and evidence-gated advancement |
| `backend/harness/connectors.py` | MCP connector access and background grants |
| `backend/routes/harness.py` | Project and task HTTP boundary |
| `scripts/panini-longrun-eval.py` | Repeated live evaluation, separate from product claims |
| `scripts/panini-release-gate.ps1` | Packaged integrity and promotion requirements |

Keep components near their owning feature. Move a feature into a directory when it needs multiple components, tests or fixtures. Avoid a repository-wide rename that changes import paths without improving a user flow.

## Acceptance gates

- Admin typecheck, build and tests pass.
- Project creation, draft, exact-revision approval, sequential advancement, pause and `needs_review` have backend coverage.
- The live evaluation records all three scheduled samples before any multi-day claim; its three research cases are not a Dot benchmark.
- Release promotion requires a clean committed source revision, verified packaged hashes, valid signing, and clean Windows VM evidence. An unsigned build is a development candidate.

## Outstanding parity gaps

PANINI does not yet provide Dots' own cloud computer, always-on multi-device continuity, broad 4,000+ app ecosystem, or mature multi-app autonomous project work. The current release lacks signing and clean-VM qualification. These require separate engineering and measured validation; matching a visual pattern does not establish product equivalence.
