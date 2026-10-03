-- V2-14: remote fabric. Devices hold their own keys (only public keys here); pushes and webhook deliveries.
CREATE TABLE IF NOT EXISTS devices_v2(id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL,
  sign_pub TEXT NOT NULL, agree_pub TEXT NOT NULL, status TEXT NOT NULL,
  created REAL NOT NULL, last_seen REAL, transports TEXT NOT NULL DEFAULT '[]', grants TEXT NOT NULL DEFAULT '{}',
  sas TEXT);
CREATE TABLE IF NOT EXISTS push_subscriptions(device_id TEXT NOT NULL, endpoint TEXT NOT NULL, p256dh TEXT NOT NULL,
  auth TEXT NOT NULL, created REAL NOT NULL, PRIMARY KEY(device_id, endpoint));
CREATE TABLE IF NOT EXISTS ingress_seen(hook TEXT NOT NULL, delivery_id TEXT NOT NULL, seen REAL NOT NULL,
  PRIMARY KEY(hook, delivery_id));
