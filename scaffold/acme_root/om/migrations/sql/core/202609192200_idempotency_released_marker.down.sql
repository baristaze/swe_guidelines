-- A released marker becomes a held one under a token nobody has, which the
-- earlier code takes over past the pending lease.

UPDATE core.idempotency_records SET attempt_id = id WHERE attempt_id IS NULL;
ALTER TABLE core.idempotency_records ALTER COLUMN attempt_id SET NOT NULL;
