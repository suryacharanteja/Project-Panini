"""Case expectations. Each returns None when satisfied, else a short reason."""

from pathlib import Path

from .cases import expand


def newest_download():
    from backend.capabilities.desktop import known_folder

    folder = known_folder("downloads")
    files = sorted(
        (p for p in folder.iterdir() if p.is_file()), key=lambda p: p.stat().st_mtime, reverse=True
    )
    return files[0].name.lower() if files else ""


def read_text(path: Path) -> str:
    """Text of a file written by any Windows tool (UTF-8, UTF-8 with BOM, or UTF-16)."""
    if not path.exists():
        return ""
    raw = path.read_bytes()
    try:
        return raw.decode("utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig")
    except UnicodeDecodeError:
        return ""


def evaluate(expect: dict, result, work: Path) -> list[str]:
    expect = expand(expect, work)
    message = (result.message or "").lower()
    compact = message.replace(",", "")
    failures = []

    def fail(reason):
        failures.append(reason)

    for key, value in expect.items():
        if key == "outcome_in" and result.outcome not in value:
            fail(f"outcome {result.outcome!r} not in {value}")
        elif key == "message_contains_all":
            missing = [w for w in value if w.lower() not in message and w.lower() not in compact]
            if missing:
                fail(f"message lacks {missing}")
        elif key == "message_contains_any":
            if not any(w.lower() in message or w.lower() in compact for w in value):
                fail(f"message lacks any of {value}")
        elif key == "message_contains_newest_download":
            newest = newest_download()
            if newest and newest[:12] not in message:
                fail("message does not name the newest download")
        elif key == "message_contains_file_count":
            count = sum(p.is_file() for p in Path(value).rglob("*"))
            if str(count) not in message:
                fail(f"message lacks the file count {count}")
        elif key == "file_exists":
            for path in value:
                if not Path(path).exists():
                    fail(f"{path} does not exist")
        elif key == "file_absent":
            for path in value:
                if Path(path).exists():
                    fail(f"{path} exists")
        elif key == "file_contains":
            for path, text in value.items():
                if text.lower() not in read_text(Path(path)).lower():
                    fail(f"{path} lacks {text!r}")
        elif key == "approvals_requested_min" and result.approvals_requested < value:
            fail(f"{result.approvals_requested} approvals requested, expected at least {value}")
        elif key == "approvals_requested_max" and result.approvals_requested > value:
            fail(f"{result.approvals_requested} approvals requested, expected at most {value}")
        elif key == "max_seconds" and result.seconds > value:
            fail(f"took {result.seconds}s, budget {value}s")
        elif key == "evidence_min" and result.evidence_count < value:
            fail(f"{result.evidence_count} evidence records, expected at least {value}")
        elif key == "task_status_in" and result.task_status not in value:
            fail(f"task status {result.task_status!r} not in {value}")
    return failures
