CREATE TABLE channel_accounts(id TEXT PRIMARY KEY, channel TEXT NOT NULL, label TEXT NOT NULL,
 config TEXT NOT NULL, persona_id TEXT NOT NULL DEFAULT 'panini', enabled INTEGER NOT NULL DEFAULT 0,
 created REAL NOT NULL, updated REAL NOT NULL);
CREATE TABLE channel_messages(account_id TEXT NOT NULL, message_id TEXT NOT NULL, seen REAL NOT NULL,
 PRIMARY KEY(account_id,message_id));
CREATE TABLE channel_jobs(task_id TEXT PRIMARY KEY, account_id TEXT NOT NULL, thread_key TEXT NOT NULL,
 thread_id TEXT NOT NULL, sender_id TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending', created REAL NOT NULL);
CREATE INDEX channel_jobs_thread ON channel_jobs(account_id,thread_key,state);
CREATE TABLE channel_outbox(id TEXT PRIMARY KEY, account_id TEXT NOT NULL, thread_key TEXT NOT NULL,
 text TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'queued', available_at REAL NOT NULL,
 provider_id TEXT, created REAL NOT NULL, error_class TEXT);
CREATE INDEX channel_outbox_due ON channel_outbox(state,available_at);
CREATE TABLE channel_state(account_id TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL,
 PRIMARY KEY(account_id,key));
