"""PANINI agent harness: memory, skills, sessions, durable tasks and the agent loop.

Everything is stored locally in one SQLite database (PANINI_DATA_DIR, default
%LOCALAPPDATA%\\PANINI\\harness.sqlite3). Retrieval uses SQLite FTS5 plus free,
local hashed n-gram embeddings: no data or vectors leave the machine.
"""
