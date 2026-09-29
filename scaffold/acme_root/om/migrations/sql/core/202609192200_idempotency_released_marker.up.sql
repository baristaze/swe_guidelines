-- A failure releases the marker by clearing its attempt, and the record
-- stays with its digest and its target id: a released marker has no attempt
-- and no outcome, and the next retry re-arms it and reruns on the same id,
-- so a row the failed attempt left behind is found, not repeated. Expand
-- only; every row written before this is held or finished.

ALTER TABLE core.idempotency_records ALTER COLUMN attempt_id DROP NOT NULL;
