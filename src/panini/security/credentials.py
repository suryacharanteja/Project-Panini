"""Encrypted credential store with handle-based access (DD-066, DD-115).

Tools declare handle *names*; the kernel resolves them to values at the tool
boundary and nowhere else. Values never enter model context, logs, the audit
trail, or exception messages (FR-542, FR-543).

The key is derived from machine identity, so a credentials file copied to
another machine cannot be decrypted. Backups of the project directory therefore
carry no usable secrets (DD-144).
"""

from __future__ import annotations

import base64
import getpass
import hashlib
import json
import os
import platform
from dataclasses import dataclass
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from panini.errors import CredentialError

PBKDF2_ITERATIONS = 600_000
SALT_SIZE = 16


@dataclass(frozen=True, slots=True)
class CredentialBundle:
    """Values handed to a tool at execution time.

    ``__repr__`` is overridden so a bundle can never leak through logging, a
    traceback, or an f-string in tool code.
    """

    _values: dict[str, str]

    def get(self, handle: str) -> str:
        try:
            return self._values[handle]
        except KeyError:
            raise CredentialError(f"credential handle {handle!r} was not provided") from None

    def __repr__(self) -> str:
        return f"CredentialBundle(handles={sorted(self._values)!r})"

    __str__ = __repr__


def _machine_id() -> bytes:
    """Stable per-machine identity (DD-066).

    Deliberately excludes anything a backup would carry, so the derived key
    does not travel with the file.
    """
    parts = [platform.node(), getpass.getuser(), platform.machine()]
    if os.name == "nt":
        parts.append(os.environ.get("COMPUTERNAME", ""))
    return hashlib.sha256("\x00".join(parts).encode("utf-8")).digest()


class CredentialStore:
    """Fernet-encrypted key/value store, bound to this machine."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._cache: dict[str, str] | None = None

    # ── key derivation ───────────────────────────────────────────────────────

    def _fernet(self, salt: bytes) -> Fernet:
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=PBKDF2_ITERATIONS,
        )
        return Fernet(base64.urlsafe_b64encode(kdf.derive(_machine_id())))

    # ── persistence ──────────────────────────────────────────────────────────

    def _load(self) -> dict[str, str]:
        if self._cache is not None:
            return self._cache
        if not self._path.exists():
            self._cache = {}
            return self._cache

        raw = self._path.read_bytes()
        if len(raw) < SALT_SIZE:
            raise CredentialError("credential store is truncated or corrupt")

        salt, payload = raw[:SALT_SIZE], raw[SALT_SIZE:]
        try:
            decrypted = self._fernet(salt).decrypt(payload)
        except InvalidToken:
            # Almost always means the file came from another machine (DD-144).
            raise CredentialError(
                "credential store could not be decrypted on this machine. "
                "Credentials are machine-bound and are not portable via backup; "
                "re-authenticate the affected connectors."
            ) from None

        self._cache = json.loads(decrypted)
        return self._cache

    def _save(self, values: dict[str, str]) -> None:
        salt = os.urandom(SALT_SIZE)
        payload = self._fernet(salt).encrypt(json.dumps(values).encode("utf-8"))
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_bytes(salt + payload)
        if os.name != "nt":
            os.chmod(tmp, 0o600)
        tmp.replace(self._path)
        self._cache = values

    # ── public API ───────────────────────────────────────────────────────────

    def set(self, handle: str, value: str) -> None:
        values = dict(self._load())
        values[handle] = value
        self._save(values)

    def delete(self, handle: str) -> None:
        values = dict(self._load())
        values.pop(handle, None)
        self._save(values)

    def has(self, handle: str) -> bool:
        return handle in self._load()

    def handles(self) -> list[str]:
        """Handle names only — never values."""
        return sorted(self._load())

    def resolve(self, handles: tuple[str, ...]) -> CredentialBundle:
        """Build the bundle passed to a tool at execution time.

        Missing handles raise here rather than inside the tool, so a connector
        never runs half-authenticated.
        """
        stored = self._load()
        missing = [h for h in handles if h not in stored]
        if missing:
            raise CredentialError(
                f"missing credential handles: {', '.join(missing)}. "
                "Configure the connector before enabling its tools."
            )
        return CredentialBundle({h: stored[h] for h in handles})
