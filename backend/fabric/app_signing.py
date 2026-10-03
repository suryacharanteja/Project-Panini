"""Signed phone-app assets (V2-14 §5.7): a compromised relay cannot serve modified JavaScript.

The PC hashes every asset of the built phone app and signs the manifest with its signing key. The service worker
(V2-15) keeps the PC key from pairing and activates an update only when the signature and every hash match;
otherwise it keeps the old version and says "Update refused: not signed by your PC."
"""

import json
from pathlib import Path

from . import crypto


def manifest(root: Path, version: str) -> dict:
    files = {}
    for path in sorted(p for p in Path(root).rglob("*") if p.is_file() and p.name != "manifest.signed.json"):
        files[path.relative_to(root).as_posix()] = crypto.b64(crypto.sha256(path.read_bytes()))
    return {"version": version, "files": files}


def canonical(data: dict) -> bytes:
    return json.dumps(data, sort_keys=True, separators=(",", ":")).encode()


def sign(identity, root: Path, version: str) -> dict:
    body = manifest(root, version)
    return {
        "manifest": body,
        "pc_id": identity.pc_id,
        "signature": crypto.sign(identity.sign_key, canonical(body)),
    }


def verify(signed: dict, pc_sign_pub: str, assets: dict[str, bytes]) -> bool:
    """What the service worker checks: the PC's signature, then every asset's hash."""
    body = signed.get("manifest") or {}
    if not crypto.verify(pc_sign_pub, canonical(body), signed.get("signature", "")):
        return False
    files = body.get("files") or {}
    return set(files) == set(assets) and all(
        crypto.b64(crypto.sha256(assets[p])) == h for p, h in files.items()
    )
