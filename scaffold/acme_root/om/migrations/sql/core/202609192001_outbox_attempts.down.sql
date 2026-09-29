ALTER TABLE core.outbox_rows DROP COLUMN failed_at;
ALTER TABLE core.outbox_rows DROP COLUMN last_error;
ALTER TABLE core.outbox_rows DROP COLUMN next_attempt_at;
ALTER TABLE core.outbox_rows DROP COLUMN attempts;
