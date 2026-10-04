"""Extend known read grants to the canonical V2-19 tool names; preserve custom restrictions."""

import json


def apply(connection):
    for row in connection.execute("SELECT id,data FROM grants").fetchall():
        grant_id, encoded = row[0], row[1]
        grant = json.loads(encoded)
        if (
            grant_id != grant.get("subject", "") + ":spaces_read"
            or not grant_id.startswith("persona:")
            or grant.get("capability") != "spaces"
            or set(grant.get("tools", [])) != {"spaces_list", "page_read", "page_search"}
        ):
            continue
        grant["tools"] += ["pages_list", "pages_read", "pages_search"]
        connection.execute("UPDATE grants SET data=? WHERE id=?", (json.dumps(grant), grant_id))
