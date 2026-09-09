"""Path resolution and confinement — shared by the filesystem and shell tools.

**Order is the control.** Getting the sequence wrong defeats the check entirely:

1. **Resolve first** — normalises ``../`` and follows symlinks and junctions, so
   the checks below see the real destination rather than the requested spelling.
2. **Deny before allow** — an allowed root could otherwise contain the audit
   database, and an allow-rule would grant access to it (FR-563).
3. **Allow** — must be under a configured root (FR-416).
4. **Classify egress after resolution** — a symlink into OneDrive must classify
   as synced (HLD §9.2).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from panini.errors import PathEscape

# Windows device names are reserved regardless of extension or directory.
_RESERVED_NAMES = frozenset({
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
})


class AccessMode(StrEnum):
    READ = "read"
    WRITE = "write"
    DELETE = "delete"


@dataclass(frozen=True, slots=True)
class ResolvedPath:
    path: Path
    is_synced: bool
    synced_root: Path | None


def _normcase(p: Path) -> str:
    return os.path.normcase(str(p))


def _is_relative_to(child: Path, parent: Path) -> bool:
    """Case-insensitive containment check.

    ``Path.is_relative_to`` is case-sensitive, which on Windows would let
    ``C:\\USERS\\...`` slip past a rule written as ``C:\\Users\\...``.
    """
    c, p = _normcase(child), _normcase(parent)
    return c == p or c.startswith(p.rstrip("\\/") + os.sep)


class PathGuard:
    """Resolves paths and enforces confinement.

    ``deny_roots`` is constructor-supplied and never read from user config —
    a user-editable deny list could simply be emptied (DD-145).
    """

    def __init__(
        self,
        allow_roots: list[Path],
        deny_roots: list[Path],
        synced_roots: list[Path] | None = None,
    ) -> None:
        self._allow = [Path(r).expanduser().resolve(strict=False) for r in allow_roots]
        self._deny = [Path(r).expanduser().resolve(strict=False) for r in deny_roots]
        self._synced = [Path(r).expanduser().resolve(strict=False) for r in (synced_roots or [])]

    @property
    def allow_roots(self) -> list[Path]:
        """Roots this guard permits. Surfaced to the model so it can pick a
        valid path rather than guessing one."""
        return list(self._allow)

    def resolve_and_check(self, raw: str, mode: AccessMode = AccessMode.READ) -> ResolvedPath:
        if not raw or not raw.strip():
            raise PathEscape("empty path")

        self._reject_windows_hazards(raw)

        # (1) Resolve. strict=False so a not-yet-created file can be written,
        #     while ``..`` and symlinks in existing components are still collapsed.
        try:
            candidate = Path(os.path.expandvars(raw)).expanduser()
            # A bare filename from the model means "in my workspace", not "in
            # whatever directory the process happens to be running from". CWD
            # anchoring would be both surprising and less safe — the process
            # could have been started anywhere.
            if not candidate.is_absolute() and self._allow:
                candidate = self._allow[0] / candidate
            resolved = candidate.resolve(strict=False)
        except (OSError, RuntimeError) as exc:
            raise PathEscape(f"could not resolve {raw!r}: {exc}") from exc

        # (2) Deny first — this ordering is what protects the audit store even
        #     if a user grants a broad allow_root.
        for denied in self._deny:
            if _is_relative_to(resolved, denied):
                raise PathEscape(
                    f"{raw!r} resolves into a protected location and cannot be accessed"
                )

        # (3) Allow.
        if not any(_is_relative_to(resolved, root) for root in self._allow):
            raise PathEscape(
                f"{raw!r} resolves outside the configured directories "
                f"({', '.join(str(r) for r in self._allow)})"
            )

        # (4) Egress classification, on the resolved path.
        synced_root = self._synced_root_for(resolved)
        return ResolvedPath(
            path=resolved, is_synced=synced_root is not None, synced_root=synced_root
        )

    def _synced_root_for(self, resolved: Path) -> Path | None:
        for root in self._synced:
            if _is_relative_to(resolved, root):
                return root
        return None

    @staticmethod
    def _reject_windows_hazards(raw: str) -> None:
        # UNC paths reach another machine. Extended-length prefixes (\\?\) share
        # the leading double separator and are refused with them — they are never
        # needed here, and allowing them would require replicating this parsing.
        if raw.startswith(("\\\\", "//")):
            raise PathEscape("UNC and extended-length paths are not permitted")

        # Alternate data streams hide content behind an innocuous filename:
        # `notes.txt:payload` reads as an ordinary text file in any listing.
        # After removing a leading drive designator, any remaining colon is a
        # stream separator.
        rest = raw
        if len(rest) >= 2 and rest[1] == ":" and rest[0].isalpha():
            rest = rest[2:]
        if ":" in rest:
            raise PathEscape("alternate data streams are not permitted")

        for part in Path(raw).parts:
            stem = part.split(".")[0].upper()
            if stem in _RESERVED_NAMES:
                raise PathEscape(f"{part!r} is a reserved device name")


def default_deny_roots(
    audit_path: Path, credentials_path: Path, extra: list[Path] | None = None
) -> list[Path]:
    """Locations no tool may reach, whatever the allow list says."""
    roots = [audit_path.parent, credentials_path.parent]
    roots.extend(extra or [])
    return roots
