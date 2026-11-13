"""Local hashed retrieval vectors, using the same algorithm as memory."""

from backend.harness.memory import _pack, embed


def apply(connection):
    connection.execute(
        "CREATE TABLE page_vectors(page_id TEXT PRIMARY KEY, revision INTEGER NOT NULL, vector BLOB NOT NULL)"
    )
    connection.execute(
        "CREATE TRIGGER page_vector_delete AFTER DELETE ON pages BEGIN DELETE FROM page_vectors WHERE page_id=old.id; END"
    )
    for row in connection.execute("SELECT id,revision,title,content FROM pages").fetchall():
        connection.execute(
            "INSERT INTO page_vectors VALUES(?,?,?)", (row[0], row[1], _pack(embed(row[2] + "\n" + row[3])))
        )
