-- Puts the index on the key alone back. It builds only while no two tenants
-- hold one key, which is the state the release before this one left.
CREATE UNIQUE INDEX uq_work_items_idempotency_key ON queue.work_items (idempotency_key);
