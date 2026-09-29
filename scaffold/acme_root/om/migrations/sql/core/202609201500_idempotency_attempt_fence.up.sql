-- The re-mint of a secret is conditional on the marker still holding the
-- attempt making the write, so the rerun's one statement reads
-- `org_id = X AND target_id = Y AND attempt_id = Z AND status IS NULL`.
-- `uq_idempotency_records_org_id_user_id_key` leads with the key the caller
-- sent and serves none of that, and the table has no index on org_id of its
-- own, so the fence would read every marker of the retention. This is the
-- index it reads instead.

CREATE INDEX ix_idempotency_records_org_id_target_id ON core.idempotency_records (org_id, target_id);
