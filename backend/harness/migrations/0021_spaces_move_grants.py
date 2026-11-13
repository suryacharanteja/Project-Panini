"""Extend the known built-in write grant, preserving custom constraints and deny rules."""

import json


def apply(connection):
    expected = {"space_create", "page_create", "page_update", "page_delete", "pages_create", "pages_edit"}
    for row in connection.execute("SELECT id,data FROM grants").fetchall():
        grant_id, encoded = row[0], row[1]
        grant = json.loads(encoded)
        if (
            grant_id == grant.get("subject", "") + ":spaces_write"
            and grant_id.startswith("persona:")
            and grant.get("capability") == "spaces"
            and set(grant.get("tools", [])) == expected
        ):
            grant["tools"] += ["pages_move", "pages_delete"]
            connection.execute("UPDATE grants SET data=? WHERE id=?", (json.dumps(grant), grant_id))
