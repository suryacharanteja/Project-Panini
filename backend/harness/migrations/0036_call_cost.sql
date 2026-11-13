-- V2-16: priced call cost (duration at the configured voice rate) and the mode an unknown caller was taken in.
ALTER TABLE calls ADD COLUMN cost_usd REAL;
ALTER TABLE calls ADD COLUMN mode TEXT NOT NULL DEFAULT 'owner';
