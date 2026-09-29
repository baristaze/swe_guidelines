-- The sweep claims pending outbox rows instead of reading them: each claim
-- spends an attempt and sets the next one with a growing delay, and a row
-- past the relay's max_attempts is failed, a dead letter, so a poison row
-- starves nothing behind it and two sweeps never relay the same batch.
-- Expand only: existing rows have spent no attempt and are claimable at once.

ALTER TABLE core.outbox_rows ADD COLUMN attempts integer NULL;
UPDATE core.outbox_rows SET attempts = 0;
ALTER TABLE core.outbox_rows ALTER COLUMN attempts SET NOT NULL;
ALTER TABLE core.outbox_rows ADD COLUMN next_attempt_at timestamptz NULL;
ALTER TABLE core.outbox_rows ADD COLUMN last_error text NULL;
ALTER TABLE core.outbox_rows ADD COLUMN failed_at timestamptz NULL;
