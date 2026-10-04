"""Owner-managed persona definitions. Permission changes advance the policy epoch."""

import re
import sqlite3
import time

from ..core_contracts import EffectClass, ToolSpec
from ..policy.engine import Grant

SLUG = re.compile(r"^[a-z][a-z0-9-]{0,40}$")
COLORS = ("copper", "cyan", "violet", "green", "amber", "rose")
FIELDS = {
    "name",
    "role",
    "instructions",
    "voice",
    "color",
    "default_space_id",
    "memory_scope",
    "memory_write",
    "computer_id",
    "model_tier",
}
# Grants permit a tool; the effect matrix still determines approval level. No wildcard grants.
PERMISSIONS = {
    "research": ("capabilities", ("search_run",)),
    "chrome": ("browser", ("browser_*",)),
    "managed": ("browser", ("browser_*",)),
    "desktop": ("desktop", ("desktop_*",)),
    "code": ("capabilities", ("code_run",)),
    "sandbox_browser": (
        "sandbox",
        (
            "sandbox_status",
            "sandbox_start",
            "sandbox_navigate",
            "sandbox_read",
            "sandbox_snapshot",
            "sandbox_screenshot",
            "sandbox_click",
            "sandbox_type",
            "sandbox_key",
            "sandbox_scroll",
        ),
    ),
    "sandbox_files": ("sandbox", ("sandbox_files_read", "sandbox_files_write", "sandbox_files_list")),
    "sandbox_shell": ("sandbox", ("sandbox_exec",)),
    "spaces_read": ("spaces", ("spaces_list", "page_read", "page_search")),
    "spaces_write": ("spaces", ("space_create", "page_create", "page_update", "page_delete")),
    "memory_read": ("memory", ("recall",)),
    "memory_write": ("memory", ("remember", "forget")),
}


class Personas:
    def presentation(self, persona_id):
        row = self.store.one("SELECT id,name,color FROM personas WHERE id=?", (persona_id,))
        return dict(row) if row else {"id": "panini", "name": "PANINI", "color": "copper"}

    def __init__(self, store, policy, *, identity_read=None, identity_write=None):
        from ..harness.learning import persona, set_persona

        self.store, self.policy = store, policy
        self.identity_read = identity_read or persona
        self.identity_write = identity_write or set_persona
        if not self.store.one("SELECT id FROM personas WHERE id='panini'"):
            self.create(
                "panini",
                name="PANINI",
                role="Personal agent",
                instructions=self.identity_read(),
                memory_scope="global",
                memory_write=True,
            )

    def list(self, *, archived=False):
        self.get("panini")  # Existing Identity editor and file edits are reflected here.
        return self.store.all(
            "SELECT * FROM personas WHERE archived<=? ORDER BY created,id", (int(archived),)
        )

    def _write(self, sql, params):
        try:
            return self.store.execute(sql, params)
        except sqlite3.IntegrityError:
            raise ValueError("That persona ID or active name already exists.") from None

    def get(self, persona_id, *, include_archived=False):
        row = self.store.one("SELECT * FROM personas WHERE id=?", (persona_id,))
        if not row or (row["archived"] and not include_archived):
            raise KeyError("Persona is unavailable.")
        if persona_id == "panini":
            text = self.identity_read()
            if text != row["instructions"]:
                self.store.execute(
                    "UPDATE personas SET instructions=?,updated=? WHERE id='panini'", (text, time.time())
                )
                row["instructions"] = text
        row["permissions"] = self.permissions(persona_id)
        return row

    @staticmethod
    def validate(values):
        if set(values) - FIELDS:
            raise ValueError("Unknown persona fields.")
        result = dict(values)
        for key, maximum in (("name", 80), ("role", 300), ("instructions", 8000)):
            if key in result:
                if not isinstance(result[key], str) or not result[key].strip() or len(result[key]) > maximum:
                    raise ValueError(f"{key} must contain 1-{maximum} characters.")
                result[key] = result[key].strip()
        if "color" in result and result["color"] not in COLORS:
            raise ValueError("Choose a supported colour.")
        if "memory_scope" in result and result["memory_scope"] not in {"global", "persona", "none"}:
            raise ValueError("Invalid memory scope.")
        if "memory_write" in result and not isinstance(result["memory_write"], bool):
            raise ValueError("memory_write must be a boolean.")
        for key in ("computer_id", "default_space_id", "voice", "model_tier"):
            if (
                key in result
                and result[key] is not None
                and (not isinstance(result[key], str) or len(result[key]) > 100)
            ):
                raise ValueError(f"Invalid {key}.")
        if result.get("computer_id") and not SLUG.fullmatch(result["computer_id"]):
            raise ValueError("Invalid computer ID.")
        if result.get("model_tier") not in {None, "fast", "agent", "deep"}:
            raise ValueError("Invalid model tier.")
        return result

    def create(self, persona_id, **values):
        if not isinstance(persona_id, str) or not SLUG.fullmatch(persona_id):
            raise ValueError("Persona IDs use lowercase letters, digits and hyphens; start with a letter.")
        data = {
            "name": persona_id,
            "role": "Specialist",
            "instructions": "Help with the assigned task.",
            "voice": None,
            "color": "copper",
            "default_space_id": None,
            "memory_scope": "persona",
            "memory_write": False,
            "computer_id": persona_id,
            "model_tier": None,
            **self.validate(values),
        }
        if self.store.one("SELECT id FROM personas WHERE id=?", (persona_id,)):
            raise ValueError("That persona ID already exists.")
        if self.store.one(
            "SELECT id FROM personas WHERE lower(name)=lower(?) AND archived=0", (data["name"],)
        ):
            raise ValueError("That persona name already exists.")
        columns = list(data)
        now = time.time()
        self._write(
            f"INSERT INTO personas(id,{','.join(columns)},created,updated) VALUES ({','.join('?' for _ in range(len(columns) + 3))})",
            (persona_id, *data.values(), now, now),
        )
        if persona_id != "panini":
            self.set_permissions(persona_id, ["research", "spaces_read"])
        return self.get(persona_id)

    def update(self, persona_id, **values):
        self.get(persona_id)
        values = self.validate(values)
        if "name" in values and self.store.one(
            "SELECT id FROM personas WHERE id!=? AND lower(name)=lower(?) AND archived=0",
            (persona_id, values["name"]),
        ):
            raise ValueError("That persona name already exists.")
        if persona_id == "panini" and "instructions" in values:
            self.identity_write(values["instructions"])
        if values:
            self._write(
                f"UPDATE personas SET {','.join(k + '=?' for k in values)},updated=? WHERE id=?",
                (*values.values(), time.time(), persona_id),
            )
            self.policy.bump()
        return self.get(persona_id)

    def archive(self, persona_id):
        if persona_id == "panini":
            raise ValueError("PANINI cannot be archived.")
        self.get(persona_id)
        self.store.execute("UPDATE personas SET archived=1,updated=? WHERE id=?", (time.time(), persona_id))
        for grant in self.policy.grants(f"persona:{persona_id}"):
            self.policy.revoke(grant.id)
        self.policy.bump()

    def permissions(self, persona_id):
        ids = {
            g.id
            for g in self.policy.grants(f"persona:{persona_id}")
            if not g.expires_at or g.expires_at > self.policy.clock()
        }
        return [key for key in PERMISSIONS if f"persona:{persona_id}:{key}" in ids]

    def set_permissions(self, persona_id, permissions):
        self.get(persona_id)
        if persona_id == "panini":
            raise ValueError("Use owner permissions for PANINI.")
        if not isinstance(permissions, list) or any(p not in PERMISSIONS for p in permissions):
            raise ValueError("Unknown persona permission.")
        for grant in self.policy.grants(f"persona:{persona_id}"):
            self.policy.revoke(grant.id)
        for permission in sorted(set(permissions)):
            capability, tools = PERMISSIONS[permission]
            self.policy.put_grant(
                Grant(
                    id=f"persona:{persona_id}:{permission}",
                    subject=f"persona:{persona_id}",
                    capability=capability,
                    tools=tools,
                    note="Persona permission",
                )
            )
        return self.get(persona_id)

    def allows(self, persona_id, spec):
        self.get(persona_id)  # Archival blocks even control tools.
        if persona_id == "panini":
            return True
        from ..core_contracts import Principal

        principal = Principal(kind="persona", client_id=persona_id)
        return any(
            g.applies({f"persona:{persona_id}"}, spec, principal, self.policy.clock())
            and g.levels.get(spec.effect.value) != "deny"
            for g in self.policy.grants(f"persona:{persona_id}")
        )

    def bind_surface(self, surface, persona_id):
        self.get(persona_id)
        if not isinstance(surface, str) or not surface.strip() or len(surface) > 150:
            raise ValueError("Invalid surface.")
        self.store.execute(
            "INSERT INTO persona_surfaces(surface,persona_id) VALUES (?,?) ON CONFLICT(surface) DO UPDATE SET persona_id=excluded.persona_id",
            (surface, persona_id),
        )

    def surface_default(self, surface):
        row = self.store.one("SELECT persona_id FROM persona_surfaces WHERE surface=?", (surface,))
        try:
            return self.get(row["persona_id"] if row else "panini")["id"]
        except KeyError:
            return "panini"

    def search_memory(self, memory, persona_id, query, **kwargs):
        row = self.get(persona_id)
        if row["memory_scope"] == "none" or (
            persona_id != "panini"
            and not self.allows(persona_id, _memory_spec("recall", EffectClass.OBSERVE))
        ):
            return []
        found = memory.search(
            query,
            scope=f"persona:{persona_id}" if row["memory_scope"] == "persona" else "global",
            global_kinds=("profile", "preference") if row["memory_scope"] == "persona" else None,
            **kwargs,
        )
        if row["memory_scope"] == "persona":
            found = [
                m
                for m in found
                if m["scope"] == f"persona:{persona_id}" or m["kind"] in {"profile", "preference"}
            ]
        return found

    def memory_destination(self, persona_id):
        row = self.get(persona_id)
        if (
            row["memory_scope"] == "none"
            or not row["memory_write"]
            or (
                persona_id != "panini"
                and not self.allows(persona_id, _memory_spec("remember", EffectClass.LOCAL_WRITE))
            )
        ):
            raise ValueError("This persona cannot write memories.")
        return f"persona:{persona_id}" if row["memory_scope"] == "persona" else "global"


def _memory_spec(name, effect):
    return ToolSpec(name, "memory", "memory", name, name, {}, effect)
