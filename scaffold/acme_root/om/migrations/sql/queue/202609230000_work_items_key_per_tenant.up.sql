-- The idempotency key collides within its tenant, as every index on a tenant
-- table leads with the tenant, so the read-back under that tenant always
-- finds the row that holds the key. Expand only: the key alone stays unique
-- beside it, because a release that predates this one may still be serving
-- while this one rolls out, and it reads a taken key as a retry. The release
-- after drops `uq_work_items_idempotency_key`.
--
-- Every row is already unique on the key alone, so none can violate the new
-- index. Building it is not a data migration, so the policy does not bear on
-- it.
CREATE UNIQUE INDEX uq_work_items_org_id_idempotency_key
    ON queue.work_items (org_id, idempotency_key);

-- The purge of done and failed items reads every tenant's in one statement,
-- by status and by the last change.
CREATE INDEX ix_work_items_status_updated_at ON queue.work_items (status, updated_at);
