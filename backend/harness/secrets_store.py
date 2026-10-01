"""Small secrets (connector OAuth tokens) at rest, encrypted for the current Windows user.

Windows: DPAPI (CryptProtectData), so only this Windows account on this PC can read
them. Elsewhere: a file readable only by the owner. Secrets are never written to
harness.sqlite3, logs or the agent's context.
"""

import ctypes
import json
import os
from pathlib import Path

from ..extension_pairing import data_directory


class _Blob(ctypes.Structure):
    _fields_ = [("size", ctypes.c_uint32), ("data", ctypes.POINTER(ctypes.c_char))]


def _dpapi(data, protect):
    crypt = ctypes.windll.crypt32
    kernel = ctypes.windll.kernel32
    buffer = ctypes.create_string_buffer(data, len(data))
    source = _Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    result = _Blob()
    call = crypt.CryptProtectData if protect else crypt.CryptUnprotectData
    # CRYPTPROTECT_UI_FORBIDDEN = 0x1
    if not call(ctypes.byref(source), None, None, None, None, 0x1, ctypes.byref(result)):
        raise OSError("Windows could not protect or read the stored secret.")
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        kernel.LocalFree(result.data)


class SecretStore:
    def __init__(self, path=None):
        self.path = Path(path) if path else data_directory() / "connector-secrets.bin"

    def _read(self):
        if not self.path.is_file():
            return {}
        raw = self.path.read_bytes()
        try:
            data = _dpapi(raw, False) if os.name == "nt" else raw
            return json.loads(data.decode("utf-8"))
        except (OSError, ValueError):
            return {}

    def _write(self, values):
        data = json.dumps(values).encode("utf-8")
        blob = _dpapi(data, True) if os.name == "nt" else data
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_bytes(blob)
        if os.name != "nt":
            os.chmod(temporary, 0o600)
        os.replace(temporary, self.path)

    def get(self, key):
        return self._read().get(key)

    def set(self, key, value):
        values = self._read()
        if value is None:
            values.pop(key, None)
        else:
            values[key] = value
        self._write(values)
