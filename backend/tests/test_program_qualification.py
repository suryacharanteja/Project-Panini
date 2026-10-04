"""Release acceptance cannot be inferred from a Done label or one successful test."""

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/panini-program-qualify.py"
spec = importlib.util.spec_from_file_location("panini_program_qualify", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def program(tmp_path, check="x", status="Done"):
    docs = tmp_path / "docs/panini-enhancements"
    (docs / "lld").mkdir(parents=True)
    (docs / "live_panini_version2_implementation_register.md").write_text(
        "\n".join(f"| V2-{i:02} | Scope | {status} |" for i in range(28))
    )
    for index in range(28):
        (docs / f"lld/V2-{index:02}-fixture.md").write_text(
            f"## 8. Acceptance criteria\n\n- [{check}] Working behavior\n- [ ] Red-team suite\n\n## 9. Rollout\n"
        )
    return docs


def test_done_does_not_pass_unchecked_acceptance(tmp_path):
    program(tmp_path, check=" ")
    assert not any(i["passed"] for i in module.audit(tmp_path))


def test_deferred_redteam_does_not_block_other_completed_requirements(tmp_path):
    program(tmp_path)
    result = module.audit(tmp_path)
    assert len(result) == 28 and all(i["passed"] for i in result)
    assert all(i["checks"][1]["deferred_by_owner"] for i in result)


def test_missing_or_duplicate_lld_fails(tmp_path):
    docs = program(tmp_path)
    (docs / "lld/V2-00-fixture.md").unlink()
    (docs / "lld/V2-01-duplicate.md").write_text("Duplicate")
    result = module.audit(tmp_path)
    assert not result[0]["passed"] and not result[1]["passed"]


def test_checked_criteria_without_completed_implementation_fail(tmp_path):
    program(tmp_path, status="In progress")
    assert not any(i["passed"] for i in module.audit(tmp_path))
