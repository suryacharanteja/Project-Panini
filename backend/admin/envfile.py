"""Edit PANINI's .env in place: keep comments and order, change only the given keys."""

import os
import re
from pathlib import Path

_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")


def _quote(value):
    if value == "" or re.fullmatch(r"[A-Za-z0-9_./:@+,=-]+", value):
        return value
    if "\\" in value:
        raise ValueError("Values with backslashes cannot be stored safely in .env; use forward slashes.")
    if "'" not in value:
        return f"'{value}'"  # Quoted, so spaces, # and JSON survive both parsers.
    return '"' + value.replace('"', '\\"') + '"'


def update(path, changes):
    """Set KEY=value for each change (None removes the key). Writes atomically."""
    path = Path(path)
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    pending = dict(changes)
    output = []
    for line in lines:
        match = _LINE.match(line)
        key = match.group(1) if match else None
        if key in pending:
            value = pending.pop(key)
            if value is not None:
                output.append(f"{key}={_quote(value)}")
            continue
        output.append(line)
    for key, value in pending.items():
        if value is not None:
            output.append(f"{key}={_quote(value)}")
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text("\n".join(output) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def read(path):
    from dotenv import dotenv_values

    path = Path(path)
    return {k: v for k, v in dotenv_values(path).items() if v is not None} if path.exists() else {}
