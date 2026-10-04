CREATE TABLE IF NOT EXISTS device_passkeys(
  credential_id TEXT PRIMARY KEY, device_id TEXT NOT NULL, public_key BLOB NOT NULL,
  sign_count INTEGER NOT NULL, origin TEXT NOT NULL, rp_id TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS passkey_ceremonies(
  id TEXT PRIMARY KEY, device_id TEXT NOT NULL, kind TEXT NOT NULL, challenge BLOB NOT NULL,
  origin TEXT NOT NULL, rp_id TEXT NOT NULL, statement_hash TEXT, expires REAL NOT NULL,
  used INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS passkeys_device ON device_passkeys(device_id);
