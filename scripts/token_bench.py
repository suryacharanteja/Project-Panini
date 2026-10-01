"""Offline token benchmark: model input before and after the Jev-first optimisation.

No network calls. Token counts use the ~4 characters/token rule; image tokens use OpenAI's
published tile costs (high: 85 + 170 per 512px tile; low: 85). Pages mirror what the
extension (panini-core/capabilities/browser/extension/content.js) and the Windows worker actually emit. Jev passage
selection is modelled at its budget (SELECTED_TEXT_CHARS), an upper bound; live runs
selected about a third of the off-screen text.

    .venv\\Scripts\\python.exe scripts/token_bench.py
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.capabilities.desktop import compact_element  # noqa: E402
from backend.extension_planner import (  # noqa: E402
    RULES,
    SELECTED_TEXT_CHARS,
    TaskPlan,
    page_text_beyond_viewport,
    proposal_payload,
)
from backend.harness.agent import INSTRUCTIONS, TOOLS, principles  # noqa: E402

HIGH_1280x800 = 85 + 170 * 4  # Scaled to 768x1229 -> 2x3 tiles; 4 is a conservative middle.
LOW = 85
STEPS = 15
TASK = "search for AI manager jobs in Dubai on LinkedIn"


def tokens(value):
    return len(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)) // 4


def action(i, nav=False):
    """One link as content.js emits it: a site menu/footer link or a job card."""
    return {
        "node": 100 + i,
        "role": "link",
        "label": f"Menu item {i}" if nav else f"Senior AI Manager at Company {i}",
        "context": "A global-nav__primary-link ember-view"
        if nav
        else "A job-card-container__link job-card-list__title--link artdeco-entity-lockup__title ember-view",
        "bounds": {"x": 24, "y": 180 + 96 * i, "width": 420, "height": 48},
        "href": f"https://www.linkedin.com/{'nav' if nav else 'jobs/view'}/40{i:04d}/?refId=abc%3D%3D&trackingId=xyz%3D%3D",
        "kind": "click",
        "value": "",
        "id": f"e{100 + i}_click",
    }


def page():
    text = " ".join(
        f"Result {i}: Senior AI Manager at Company {i}, Dubai, posted {i} days ago." for i in range(220)
    )
    viewport = text[:3000]
    return {
        "tab_id": 1,
        "url": "https://www.linkedin.com/jobs/search/?keywords=AI%20manager",
        "title": "AI manager jobs",
        # As extension_loop builds it: the viewport text, then the whole page's text.
        "text": "\n".join(dict.fromkeys([viewport, text]))[:16000],
        "viewport_text": viewport,
        # 118 controls: 70 site menu, filter and footer links, then 48 job cards.
        "actions": [action(i, nav=i < 70) for i in range(118)]
        + [{"id": "scroll_down", "kind": "scroll", "label": "Scroll down", "delta": 560}],
        "workspace": {"tabs": [{"id": 1}, {"id": 2}]},
        "visited_pages": [
            {"tab_id": 1, "url": "https://www.linkedin.com/jobs", "title": "Jobs", "text": text[:4000]},
            {"tab_id": 2, "url": "https://example.com", "title": "Notes", "text": text[:4000]},
        ],
    }


def before(p, history, plan):
    return {
        "instruction": TASK,
        "task_plan": plan.model_dump(),
        "verified_searches": {},
        "navigation_capability": {"action_id": "navigate", "available": True},
        "page": {
            k: p.get(k)
            for k in ("url", "title", "text", "viewport_text", "actions", "workspace", "visited_pages")
        },
        "previous_outcomes": history[-8:],
    }


def browser():
    plan = TaskPlan(objective="Search AI manager jobs in Dubai", searches=[])
    rules = tokens(RULES)
    old_total = new_total = 0
    for step in range(1, STEPS + 1):
        history = [
            f"Clicked job card {i}: opened details and read the description of the role." for i in range(step)
        ]
        p = page()
        old = rules + tokens(before(p, history, plan)) + HIGH_1280x800
        # After: low-detail screenshot unless the last step failed (assume 1 in 5 does), and
        # Jev-selected off-screen passages at their budget.
        image = HIGH_1280x800 if step % 5 == 0 else LOW
        selected = (page_text_beyond_viewport(p)[:SELECTED_TEXT_CHARS], "jev_selected")
        # Live runs kept every job card plus ~8 filter links and dropped menus and footers.
        shown = {a["id"] for a in p["actions"] if "jobs/view" in a.get("href", "") or a["kind"] != "click"}
        shown |= {a["id"] for a in p["actions"][:8]}
        new = rules + tokens(proposal_payload(TASK, p, history, plan, {}, selected, shown)) + image
        old_total, new_total = old_total + old, new_total + new
    cached = rules * (STEPS - 1)  # Stable rules prefix is served from OpenAI's prompt cache after step 1.
    old_cost = old_total * 2 / 1e6
    new_cost = (new_total - cached) * 2 / 1e6 + cached * 0.5 / 1e6
    print(f"Browser proposer input over {STEPS} steps (one task, GPT-4.1):")
    print(f"  before : {old_total:>8,} tokens  ${old_cost:.4f}")
    print(
        f"  after  : {new_total:>8,} tokens  ${new_cost:.4f}  ({1 - new_total / old_total:.0%} fewer tokens)"
    )


def desktop():
    elements = [
        {
            "element_id": f"42.{i}.99.3",
            "name": f"Button {i}",
            "automation_id": f"num{i}Button" if i % 2 else "",
            "type": "ControlType.Button",
            "enabled": True,
            "value": None,
            "bounds": {"x": 10 * i, "y": 300, "width": 70, "height": 50},
            "patterns": ["InvokePatternIdentifiers.Pattern", "LegacyIAccessiblePatternIdentifiers.Pattern"],
        }
        for i in range(80)
    ]
    old = tokens([{**e, "element_id": f"control_{i}"} for i, e in enumerate(elements)])
    new = tokens([compact_element(e, f"control_{i}") for i, e in enumerate(elements)])
    print(
        f"Desktop observation (80 controls), every step: {old:,} -> {new:,} tokens ({1 - new / old:.0%} fewer)"
    )


def agent():
    old = tokens(INSTRUCTIONS) + tokens(TOOLS)
    # A typical research goal: Jev leaves out memory, scheduling, sub-agent, agent and skill-creation tools.
    kept = [
        t for t in TOOLS if t["name"] in {"update_plan", "use_capability", "load_skill", "ask_user", "finish"}
    ]
    new = tokens(principles({t["name"] for t in kept})) + tokens(kept)
    print(
        f"Agent instructions + tool schemas, every turn: {old:,} -> {new:,} tokens ({1 - new / old:.0%} fewer)"
    )
    print("Agent: one full model turn saved whenever Jev confirms a verified step finished the goal.")


def main():
    browser()
    desktop()
    agent()
    print("Voice turn: 16,000 -> 4,000 characters of duplicated transcript (~3,000 tokens per turn).")
    print(
        "PANINI-CLI: context budget 200k -> 96k tokens; command output 16k -> 10k chars; reads 100k -> 50k chars."
    )


if __name__ == "__main__":
    main()
