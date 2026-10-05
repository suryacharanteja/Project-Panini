CREATE TABLE mcp_clients (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, token_hash TEXT NOT NULL UNIQUE,
  grants TEXT NOT NULL, created REAL NOT NULL, last_used REAL, revoked REAL
);
CREATE INDEX mcp_clients_active ON mcp_clients(token_hash) WHERE revoked IS NULL;
