"""Client credentials: one-time tokens, hash-only database, explicit read scopes."""

import hashlib
import json
import os
import secrets
import time
from pathlib import Path
from uuid import uuid4

DEFAULT_SCOPES = ("desktop", "browser", "search")
SCOPES = (*DEFAULT_SCOPES, "chrome", "pages")


def token_hash(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def token_directory():
    if os.name == "nt":
        return Path(os.environ["LOCALAPPDATA"]) / "PANINI" / "mcp"
    return Path.home() / ".local" / "share" / "PANINI" / "mcp"


def protect_directory(path):
    """Create a directory atomically with a protected, current-user-only ACL."""
    if os.name != "nt":
        path.mkdir(mode=0o700)
        return
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    advapi.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi.GetTokenInformation.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_void_p,
    ]
    token, descriptor, sid_string = wintypes.HANDLE(), ctypes.c_void_p(), wintypes.LPWSTR()

    class Attributes(ctypes.Structure):
        _fields_ = [("length", wintypes.DWORD), ("descriptor", ctypes.c_void_p), ("inherit", wintypes.BOOL)]

    kernel.CreateDirectoryW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(Attributes)]
    try:
        if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), 8, ctypes.byref(token)):
            raise ctypes.WinError(ctypes.get_last_error())
        size = wintypes.DWORD()
        advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
        data = ctypes.create_string_buffer(size.value)
        if not advapi.GetTokenInformation(token, 1, data, size, ctypes.byref(size)):
            raise ctypes.WinError(ctypes.get_last_error())
        sid = ctypes.c_void_p.from_buffer(data).value
        if not advapi.ConvertSidToStringSidW(sid, ctypes.byref(sid_string)):
            raise ctypes.WinError(ctypes.get_last_error())
        sddl = f"D:P(A;OICI;FA;;;{sid_string.value})"
        if not advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            sddl, 1, ctypes.byref(descriptor), None
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        attributes = Attributes(ctypes.sizeof(Attributes), descriptor, False)
        if not kernel.CreateDirectoryW(str(path), ctypes.byref(attributes)):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        if descriptor:
            kernel.LocalFree(descriptor)
        if sid_string:
            kernel.LocalFree(ctypes.cast(sid_string, ctypes.c_void_p))
        if token:
            kernel.CloseHandle(token)


class Clients:
    def __init__(self, store, *, directory=None, clock=time.time):
        self.store, self.clock = store, clock
        self.directory = Path(directory) if directory is not None else token_directory()

    @staticmethod
    def scopes(values):
        if not isinstance(values, (list, tuple)) or any(v not in SCOPES for v in values):
            raise ValueError("Choose supported MCP scopes: " + ", ".join(SCOPES))
        if "chrome" in values and "browser" not in values:
            raise ValueError("Chrome also requires the browser scope.")
        return sorted(set(values))

    def create(self, name, grants=DEFAULT_SCOPES, *, save_token=True):
        name = " ".join(str(name).split())
        if not 1 <= len(name) <= 80:
            raise ValueError("Name the calling client (1–80 characters).")
        grants = self.scopes(grants)
        identifier, token = uuid4().hex, "mcp_" + secrets.token_urlsafe(32)
        path = None
        if save_token:
            # Fresh random child avoids preserving explicit ACLs on an existing directory.
            self.directory.mkdir(parents=True, exist_ok=True)
            folder = self.directory / identifier
            try:
                protect_directory(folder)
                path = folder / "client.token"
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as stream:
                    stream.write(token)
            except BaseException:
                if path:
                    path.unlink(missing_ok=True)
                if folder.is_dir():
                    folder.rmdir()
                raise
        try:
            self.store.execute(
                "INSERT INTO mcp_clients(id,name,token_hash,grants,created) VALUES (?,?,?,?,?)",
                (identifier, name, token_hash(token), json.dumps(grants), self.clock()),
            )
        except BaseException:
            if path:
                path.unlink(missing_ok=True)
                path.parent.rmdir()
            raise
        return {
            "id": identifier,
            "name": name,
            "grants": grants,
            "token": token,
            "token_file": str(path) if path else None,
        }

    def get(self, identifier):
        row = self.store.one(
            "SELECT id,name,grants,created,last_used,revoked FROM mcp_clients WHERE id=?", (identifier,)
        )
        if row:
            row["grants"] = json.loads(row["grants"])
        return row

    def list(self):
        return [self.get(r["id"]) for r in self.store.all("SELECT id FROM mcp_clients ORDER BY created DESC")]

    def authenticate(self, authorization):
        if not authorization.startswith("Bearer ") or len(authorization) > 256:
            return None
        row = self.store.one(
            "SELECT id FROM mcp_clients WHERE token_hash=? AND revoked IS NULL",
            (token_hash(authorization[7:]),),
        )
        if not row:
            return None
        self.store.execute(
            "UPDATE mcp_clients SET last_used=? WHERE id=? AND revoked IS NULL", (self.clock(), row["id"])
        )
        client = self.get(row["id"])
        return client if client and client["revoked"] is None else None

    def revoke(self, identifier):
        changed = self.store.execute(
            "UPDATE mcp_clients SET revoked=? WHERE id=? AND revoked IS NULL", (self.clock(), identifier)
        ).rowcount
        if changed:
            path = self.directory / identifier / "client.token"
            if path.is_file() and not path.is_symlink():
                path.unlink()
        return bool(changed)
