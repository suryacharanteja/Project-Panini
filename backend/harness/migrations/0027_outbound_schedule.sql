ALTER TABLE outbound_briefs ADD COLUMN schedule TEXT;
CREATE INDEX outbound_briefs_scheduled ON outbound_briefs(state,updated);
