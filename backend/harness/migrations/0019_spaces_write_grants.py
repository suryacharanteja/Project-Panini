"""Canonical aliases for existing explicit page-write permissions."""

import json


def apply(connection):
    for row in connection.execute("SELECT id,data FROM grants").fetchall():
        grant_id, encoded = row[0], row[1]
        grant = json.loads(encoded)
        if (
            grant_id != grant.get("subject", "") + ":spaces_write"
            or not grant_id.startswith("persona:")
            or grant.get("capability") != "spaces"
            or set(grant.get("tools", [])) != {"space_create", "page_create", "page_update", "page_delete"}
        ):
            continue
        grant["tools"] += ["pages_create", "pages_edit"]
        connection.execute("UPDATE grants SET data=? WHERE id=?", (json.dumps(grant), grant_id))
