"""Versioned review proposals; SQLite is authoritative and disk edits cannot alter replay."""

import json
import os
import time
from pathlib import Path
from uuid import uuid4

from ..interrupts import APPROVAL_SCHEMA, InterruptSpec, digest
from ..policy.effects import classify
from .schema import NAME, Replay


class Bundles:
    def __init__(self, harness):
        self.h, self.store = harness, harness.store
        self.root = Path(harness.skills.roots[-2][1])
        harness.interrupts.listeners.append(self.on_review)

    def current(self, name):
        row = self.store.one(
            "SELECT * FROM teach_versions WHERE name=? AND state='approved' ORDER BY version DESC LIMIT 1",
            (name,),
        )
        if row is None:
            raise ValueError("No approved demonstration skill with that name.")
        data = json.loads(row["data"])
        if digest(data) != row["digest"]:
            raise ValueError("The skill version failed its integrity check.")
        return row, Replay.model_validate(data)

    def retire(self, name):
        pending = self.store.all(
            "SELECT review_id FROM teach_versions WHERE name=? AND state='pending'", (name,)
        )
        self.store.execute(
            "UPDATE teach_versions SET state='retired' WHERE name=? AND state IN ('approved','pending')",
            (name,),
        )
        for row in pending:
            self.h.interrupts.cancel(row["review_id"], "The owner deleted this skill.")
        replay = self.h.teach.replayer
        if replay.active and replay.active_name == name:
            replay.active.cancel()

    def propose(self, name, description, payload, *, base_version=0, run_id=None):
        if not NAME.fullmatch(name) or not description.strip() or len(description) > 300:
            raise ValueError("Use a lowercase skill name and a short description.")
        replay = Replay.model_validate(payload)
        if not replay.expect:
            raise ValueError("Add at least one observable expectation before review.")
        if any(s.tool == "manual" for s in replay.steps):
            raise ValueError("Correct or remove manual steps before saving a replayable skill.")
        for step in replay.steps + replay.expect:
            if step.target and step.target.frame:
                raise ValueError(
                    "Frame-scoped replay is not qualified yet. Keep this draft until frame support is available."
                )
            spec = self.h.registry.specs.get(step.tool)
            if not spec:
                raise ValueError("A required browser tool is unavailable.")
            args = dict(step.args)
            if step.target and step.tool not in {"browser_press_key", "browser_file_upload"}:
                args.update(target="e1", element="reviewed target")
            error = self.h.registry.validate(spec, args)
            if error:
                raise ValueError(error)
        existing = next((s for s in self.h.skills.catalog() if s["name"] == name), None)
        if existing and (existing["origin"] != "user" or base_version == 0):
            raise ValueError("This name already belongs to a skill. Choose another name.")
        data = replay.model_dump()
        for identifier in replay.recording_artifacts.values():
            found = self.h.artifacts.get(identifier)
            if not found or found[0]["mime"] not in {"image/png", "text/plain"}:
                raise ValueError("Recording evidence is unavailable.")
            if not self.store.one("SELECT id FROM teach_recordings WHERE id=?", (found[0]["run_id"],)):
                raise ValueError("The artifact does not belong to a recording.")
        with self.store.lock:
            self.store.execute("BEGIN IMMEDIATE")
            try:
                latest = (
                    self.store.one("SELECT MAX(version) AS n FROM teach_versions WHERE name=?", (name,))["n"]
                    or 0
                )
                approved = (
                    self.store.one(
                        "SELECT MAX(version) AS n FROM teach_versions WHERE name=? AND state='approved'",
                        (name,),
                    )["n"]
                    or 0
                )
                if approved != base_version:
                    raise ValueError("The approved skill changed. Reload before proposing an update.")
                version = latest + 1
                action = {
                    "type": "teach_version",
                    "name": name,
                    "version": version,
                    "base_version": base_version,
                    "digest": digest(data),
                }
                review = self.h.interrupts.open(
                    InterruptSpec(
                        "review",
                        "teach_version",
                        f"{'Update' if base_version else 'Save'} skill {name}",
                        "Review the steps, inputs and expected results. Each replay effect still needs its own permission.",
                        preview={
                            "description": description,
                            "replay": data,
                            "effects": [
                                {
                                    "id": s.id,
                                    "effect": classify(
                                        self.h.registry.specs[s.tool],
                                        {**s.args, "element": s.target.name if s.target else ""},
                                    )[0].value,
                                }
                                for s in replay.steps
                            ],
                            "base_version": base_version,
                        },
                        action=action,
                        response_schema=APPROVAL_SCHEMA,
                        ttl_s=86400,
                    ),
                    run_id=run_id or f"teach:{name}:{version}",
                    owner_principal={"kind": "owner", "surface": "avatar"},
                )
                self.store.execute(
                    "INSERT INTO teach_versions(name,version,digest,data,description,state,review_id,base_version,created) VALUES (?,?,?,?,?,'pending',?,?,?)",
                    (
                        name,
                        version,
                        digest(data),
                        json.dumps(data),
                        description.strip(),
                        review["id"],
                        base_version,
                        time.time(),
                    ),
                )
                self.store.execute("COMMIT")
            except BaseException:
                self.store.execute("ROLLBACK")
                raise
        return {"name": name, "version": version, "state": "pending", "review": review}

    def on_review(self, event, view):
        if view.get("reason") == "teach_version" and view.get("status") != "open":
            self.reconcile()

    def reconcile(self):
        rows = self.store.all("SELECT * FROM teach_versions WHERE state='pending' ORDER BY created")
        for row in rows:
            view = self.h.interrupts.get(row["review_id"])
            if view["status"] == "open":
                continue
            action = {
                "type": "teach_version",
                "name": row["name"],
                "version": row["version"],
                "base_version": row["base_version"],
                "digest": row["digest"],
            }
            with self.store.lock:
                latest = (
                    self.store.one(
                        "SELECT MAX(version) AS n FROM teach_versions WHERE name=? AND state='approved'",
                        (row["name"],),
                    )["n"]
                    or 0
                )
                accepted = (
                    view["status"] == "resolved"
                    and bool((view.get("decision") or {}).get("payload", {}).get("approved"))
                    and view["action_digest"] == digest(action)
                    and latest == row["base_version"]
                    and digest(json.loads(row["data"])) == row["digest"]
                )
                self.store.execute(
                    "UPDATE teach_versions SET state=?,approved=? WHERE name=? AND version=? AND state='pending'",
                    (
                        "approved" if accepted else "rejected",
                        time.time() if accepted else None,
                        row["name"],
                        row["version"],
                    ),
                )
            if accepted:
                self.materialize(row["name"])

    def materialize(self, name):
        row, replay = self.current(name)
        folder = self.root / name
        folder.mkdir(parents=True, exist_ok=True)
        # Existing links must not redirect an owner-reviewed bundle outside the user root.
        if folder.resolve().parent != self.root.resolve():
            raise ValueError("The skill folder is outside the user skill root.")
        instructions = "\n".join(
            f"{i}. {s.tool}: {s.target.name if s.target else ''} {json.dumps(s.args, ensure_ascii=False)}"
            for i, s in enumerate(replay.steps, start=1)
        )
        expectations = "\n".join(
            f"- {s.tool}: {json.dumps(s.args, ensure_ascii=False)}" for s in replay.expect
        )
        manifest = f"---\nname: {name}\ndescription: {' '.join(row['description'].split())}\nkind: browser\nversion: {row['version']}\n---\n\nWhen Teach is enabled, use `use_skill` with this name and reviewed inputs. Otherwise follow the procedure below through available tools. Each step applies current permissions; stop on ambiguous targets or unknown effects.\n\n## Procedure\n\n{instructions}\n\n## Expected results\n\n{expectations}\n\n## Inputs\n\n```json\n{json.dumps({k: v.model_dump() for k, v in replay.inputs.items()}, indent=2)}\n```\n"
        history = self.store.all(
            "SELECT version,approved,review_id,digest FROM teach_versions WHERE name=? AND state='approved' ORDER BY version DESC",
            (name,),
        )
        changelog = (
            "# Reviewed versions\n\n"
            + "\n".join(
                f"- v{h['version']}: owner review {h['review_id']}; SHA-256 {h['digest']}" for h in history
            )
            + "\n"
        )
        for filename, content in {
            "SKILL.md": manifest,
            "replay.json": json.dumps(replay.model_dump(), ensure_ascii=False, indent=2) + "\n",
            "CHANGELOG.md": changelog,
        }.items():
            temporary = folder / (filename + "." + uuid4().hex + ".tmp")
            with temporary.open("x", encoding="utf-8") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, folder / filename)
        recording = folder / "recording"
        recording.mkdir(exist_ok=True)
        if recording.resolve().parent != folder.resolve():
            raise ValueError("The recording folder is outside the skill bundle.")
        for label, identifier in replay.recording_artifacts.items():
            found = self.h.artifacts.get(identifier)
            if found:
                row, content = found
                suffix = ".png" if row["mime"] == "image/png" else ".txt"
                target = recording / (label + suffix)
                if target.is_symlink():
                    raise ValueError("Recording evidence cannot overwrite a linked file.")
                target.write_bytes(content)
                self.h.artifacts.pin(identifier)

    def list(self):
        self.reconcile()
        rows = self.store.all("SELECT * FROM teach_versions ORDER BY name,version DESC")
        output = []
        for name in sorted({r["name"] for r in rows}):
            versions = [r for r in rows if r["name"] == name]
            counts = self.store.one(
                "SELECT COUNT(*) AS runs,COALESCE(SUM(state='passed'),0) AS passes,COALESCE(SUM(heals),0) AS heals FROM skill_runs WHERE name=?",
                (name,),
            )
            runs = self.store.all(
                "SELECT * FROM skill_runs WHERE name=? ORDER BY started DESC LIMIT 100", (name,)
            )
            failures = 0
            for run in runs:
                if run["state"] in {"passed", "running"}:
                    break
                failures += 1
            output.append(
                {
                    "name": name,
                    "versions": [
                        {k: r[k] for k in ("version", "state", "review_id", "description", "digest")}
                        for r in versions
                    ],
                    **counts,
                    "needs_attention": failures >= 3,
                    "last_failure": next((r["failure"] for r in runs if r["failure"]), ""),
                }
            )
        return output
