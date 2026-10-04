"""Skills: procedures in the Agent Skills format (a folder with SKILL.md), loaded on demand.

Progressive disclosure: only each skill's name and description sit in the prompt;
the agent loads the body with load_skill when a skill is relevant. Sources, in
priority order: project (<workspace>/.panini/skills), user (PANINI_DATA_DIR/skills)
and built-in (backend/harness/builtin_skills).
"""

import re
from pathlib import Path

from ..extension_pairing import data_directory

BUILTIN = Path(__file__).parent / "builtin_skills"
_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")


def parse(text):
    """Split YAML-style frontmatter (name, description) from the body."""
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.S)
    if not match:
        return {}, text
    meta = {}
    for line in match.group(1).splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            meta[key.strip()] = value.strip().strip("\"'")
    return meta, match.group(2)


class Skills:
    def __init__(self, user_root=None, workspace=None):
        self.roots = []
        if workspace:
            self.roots.append(("project", Path(workspace) / ".panini" / "skills"))
        self.roots.append(("user", Path(user_root) if user_root else data_directory() / "skills"))
        self.roots.append(("builtin", BUILTIN))
        self.on_delete = None

    def catalog(self):
        found = {}
        for origin, root in self.roots:
            if not root.is_dir():
                continue
            for manifest in sorted(root.glob("*/SKILL.md")):
                meta, _ = parse(manifest.read_text(encoding="utf-8"))
                name = meta.get("name") or manifest.parent.name
                if _NAME.match(name) and name not in found and meta.get("description"):
                    found[name] = {
                        "name": name,
                        "description": meta["description"][:300],
                        "origin": origin,
                        "path": str(manifest.parent),
                        "replay": (manifest.parent / "replay.json").is_file(),
                    }
        return list(found.values())

    def prompt(self):
        skills = self.catalog()
        if not skills:
            return ""
        lines = "\n".join(f"- {s['name']}: {s['description']}" for s in skills)
        return "Available skills (call load_skill with the name when one fits the task):\n" + lines

    def load(self, name):
        skill = next((s for s in self.catalog() if s["name"] == name), None)
        if not skill:
            raise KeyError(f"No skill named {name}.")
        folder = Path(skill["path"])
        _, body = parse((folder / "SKILL.md").read_text(encoding="utf-8"))
        resources = sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file())
        return {**skill, "instructions": body.strip()[:20000], "resources": resources[:50]}

    def delete(self, name):
        """Remove one of the person's own skills (built-in and project skills are read-only)."""
        import shutil

        skill = next((s for s in self.catalog() if s["name"] == name), None)
        if not skill:
            raise KeyError(f"No skill named {name}.")
        if skill["origin"] != "user":
            raise ValueError(f"{name} is a {skill['origin']} skill and cannot be deleted here.")
        shutil.rmtree(skill["path"])
        if self.on_delete:
            self.on_delete(name)

    def create(self, name, description, instructions):
        """Save a user skill (after the person reviewed the draft)."""
        if not _NAME.match(name):
            raise ValueError("Skill names use lowercase letters, digits and hyphens.")
        if not description.strip() or not instructions.strip():
            raise ValueError("A skill needs a description and instructions.")
        folder = self.roots[-2][1] / name  # The user root.
        if (folder / "replay.json").exists():
            raise ValueError("This is a reviewed demonstration. Propose a new version in Teach instead.")
        folder.mkdir(parents=True, exist_ok=True)
        text = f"---\nname: {name}\ndescription: {' '.join(description.split())}\n---\n\n{instructions.strip()}\n"
        (folder / "SKILL.md").write_text(text, encoding="utf-8")
        return self.load(name)
