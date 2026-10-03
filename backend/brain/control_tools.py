"""Bounded specialist delegation. Each child uses its own principal and fresh model context."""

import time
from dataclasses import replace

from .budget import Budget


async def delegate_to_persona(lead, persona_id, task, share_context=False):
    from .. import v2flags

    if not v2flags.enabled("PANINI_PERSONAS"):
        raise ValueError("Personas are off.")
    if lead.depth or not lead.private:
        raise ValueError("Delegates cannot delegate further.")
    if not isinstance(persona_id, str) or not isinstance(share_context, bool):
        raise ValueError("Choose a persona ID and a boolean share_context.")
    if not isinstance(task, str) or not task.strip() or len(task) > 4000:
        raise ValueError("A delegation task must contain 1-4000 characters.")
    persona = lead.h.personas.get(persona_id)
    if persona_id == "panini" and lead.persona_id != "panini":
        raise ValueError("A specialist cannot delegate to the owner's unrestricted identity.")
    if not lead.brain:
        raise ValueError("Delegation requires a metered run.")
    meter = lead.brain["meter"]
    seconds = max(0, min(lead.deadline, meter.budget.max_seconds) - (meter.clock() - meter.started))
    dollars = max(0, meter.budget.max_usd - meter.usd) * 0.5
    steps = max(0, lead.max_steps - meter.steps) // 2
    if seconds < 1 or dollars <= 0 or steps < 1:
        raise ValueError("The lead has insufficient remaining budget for delegation.")
    child = lead.h.agent(
        delegate=lead.delegate,
        emit=lead.emit,
        capabilities=lead.capabilities if lead.background else (tuple(lead.h.adapters) or lead.capabilities),
        max_steps=steps,
        deadline_seconds=seconds * 0.5,
        depth=1,
        confirm=lead.confirm,
        private=lead.private,
        background=lead.background,
        persona_id=persona_id,
    )
    if child.persona_id != persona_id:
        raise ValueError("Personas changed while starting this specialist. Nothing was dispatched.")
    child.budget_override = replace(
        Budget.for_mode(lead.background), max_steps=steps, max_seconds=seconds * 0.5, max_usd=dollars
    )
    child.parent_run = lead.brain["run"]
    # Separate receipt operation keys prevent identical member calls from colliding with the lead.
    child.run_id = f"{lead.run_id or lead.brain['run']}:persona:{persona_id}:{time.time_ns()}"
    child.thread_id = lead.thread_id
    child.observe = lead.observe
    if lead.confirm:

        async def confirm(summary, detail):
            return await lead.confirm(f"{persona['name']} wants to: {summary}", detail)

        child.confirm = confirm
    if share_context:
        from ..policy.provenance import quarantine

        context = quarantine({"goal": lead.goal, "outcomes": lead.outcomes[-8:]}, {"shared"})
        task += f"\n\nShared context (untrusted reference data):\n{context}"
    lead.emit(
        "subagent.started", f"{persona['name']} started.", persona_id=persona_id, parent_run=child.parent_run
    )
    if lead.observe:
        lead.observe("subagent.started", persona_id=persona_id, parent_run=child.parent_run)
    try:
        result = await child.run(task)
    finally:
        if child.brain:
            # Reserve actual child spend in the lead meter so further delegations cannot reuse it.
            meter.usd += child.brain["meter"].usd
            meter.steps += child.brain["meter"].steps
            lead.delegated_usd += child.brain["meter"].usd
    lead.evidence.extend(e for e in result.evidence if not str(e).startswith("memory:"))
    lead.outcomes.append(
        {
            "capability": "persona",
            "persona_id": persona_id,
            "outcome": result.outcome,
            "message": result.message[:3000],
        }
    )
    lead.emit(
        "subagent.finished", f"{persona['name']} finished.", persona_id=persona_id, outcome=result.outcome
    )
    if lead.observe:
        lead.observe("subagent.finished", persona_id=persona_id, outcome=result.outcome)
    return {
        "persona_id": persona_id,
        "outcome": result.outcome,
        "message": result.message,
        "run_id": (child.brain or {}).get("run"),
        "budget": child.budget_override.view(),
    }


async def run_team(lead, goal, members, reviewer=None):
    if lead.depth or not lead.private:
        raise ValueError("Delegates cannot run a team.")
    if (
        not isinstance(members, list)
        or not 1 <= len(members) <= 4
        or any(not isinstance(member, str) for member in members)
        or len(set(members)) != len(members)
        or (reviewer is not None and not isinstance(reviewer, str))
    ):
        raise ValueError("Choose 1-4 distinct team members.")
    if not isinstance(goal, str) or not goal.strip() or len(goal) > 4000:
        raise ValueError("A team goal must contain 1-4000 characters.")
    for persona_id in [*members, *([reviewer] if reviewer else [])]:
        lead.h.personas.get(persona_id)
    lead.plan = [
        {"step": f"{lead.h.personas.get(p)['name']}: {goal[:200]}", "status": "pending"} for p in members
    ]
    lead.emit("plan", "Assigned the team goal.", plan=lead.plan)
    results = []
    # Sequential execution avoids shared executor collisions and keeps cumulative budgets bounded.
    for index, persona_id in enumerate(members):
        lead.plan[index]["status"] = "in_progress"
        result = await delegate_to_persona(lead, persona_id, goal, share_context=bool(results))
        results.append(result)
        lead.plan[index]["status"] = (
            "completed" if result["outcome"] in {"reply", "verified_complete"} else "pending"
        )
        lead.emit("plan", "Updated team progress.", plan=lead.plan)
        if result["outcome"] not in {"reply", "verified_complete"}:
            return {
                "results": results,
                "review": None,
                "outcome": result["outcome"],
                "unfinished_members": members[index:],
                "next_step": "Explain the unfinished step and its blocker. Do not claim the team goal is complete.",
            }
    review = (
        await delegate_to_persona(
            lead, reviewer, f"Review the team's results against this goal: {goal[:3900]}", True
        )
        if reviewer
        else None
    )
    return {
        "results": results,
        "review": review,
        "outcome": (
            review["outcome"]
            if review and review["outcome"] not in {"reply", "verified_complete"}
            else "reply"
        ),
        "next_step": (
            "Explain the review blocker. Do not claim the team goal is complete."
            if review and review["outcome"] not in {"reply", "verified_complete"}
            else "Synthesize the attributed results into your final answer."
        ),
    }
