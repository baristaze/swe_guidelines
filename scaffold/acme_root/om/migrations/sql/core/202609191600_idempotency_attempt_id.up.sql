-- The marker carries the token of the attempt that holds it: begin mints
-- one, a take-over stamps a new one, and finish and release are conditional
-- on it, so an attempt that ran past its pending lease cannot finish or
-- release the marker a retry took over. Existing rows are finished and never
-- finished again; the backfill names any token.

ALTER TABLE core.idempotency_records ADD COLUMN attempt_id uuid NULL;
UPDATE core.idempotency_records SET attempt_id = id;
ALTER TABLE core.idempotency_records ALTER COLUMN attempt_id SET NOT NULL;
