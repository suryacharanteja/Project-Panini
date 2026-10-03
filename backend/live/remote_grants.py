"""Remote viewing grants (V2-12 §5.6): which hands phones and the relay may watch and control.

Frames of your own Chrome and desktop are personal, so remote surfaces see them only after you turn them on.
Local surfaces (the console and the desktop shell on this PC) are not affected.
"""

import json
from pathlib import Path

DEFAULTS = {"sandbox": True, "managed": True, "chrome": False, "desktop": False, "android": True}


class Grants:
    def __init__(self, path: Path | None = None):
        self._path = path

    @property
    def path(self) -> Path:
        if self._path is None:
            from ..extension_pairing import data_directory

            self._path = data_directory() / "live-remote-grants.json"
        return self._path

    def all(self) -> dict:
        try:
            saved = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            saved = {}
        return {hand: bool(saved.get(hand, default)) for hand, default in DEFAULTS.items()}

    def allowed(self, hand: str) -> bool:
        return self.all().get(hand, False)

    def set(self, patch: dict) -> dict:
        if set(patch) - set(DEFAULTS) or not all(isinstance(v, bool) for v in patch.values()):
            raise ValueError("Grants are chrome, managed, sandbox, desktop and android (true or false).")
        value = {**self.all(), **patch}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(value), encoding="utf-8")
        return value
