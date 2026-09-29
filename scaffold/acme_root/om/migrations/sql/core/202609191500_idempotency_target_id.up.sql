-- The id the create uses travels on the marker, minted before it, so a retry
-- that takes over an abandoned marker creates on the same id and cannot
-- duplicate what a crash between commit and finish left behind. Existing
-- rows are finished and never rerun; the backfill names any id.

ALTER TABLE core.idempotency_records ADD COLUMN target_id uuid NULL;
UPDATE core.idempotency_records SET target_id = id;
ALTER TABLE core.idempotency_records ALTER COLUMN target_id SET NOT NULL;
