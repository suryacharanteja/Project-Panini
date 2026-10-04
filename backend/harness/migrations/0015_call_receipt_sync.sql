-- V2-16: durable receipt delivery independently of transcript finalization.
ALTER TABLE calls ADD COLUMN receipt_synced INTEGER NOT NULL DEFAULT 0;
