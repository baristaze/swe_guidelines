-- The sweep requeues expired leases across tenants in one statement, so its
-- index leads with the status and the expiry, not the tenant. Nothing reads
-- the per-tenant index after that, so it goes. org_id still leads the unique
-- index on the idempotency key. Plain CREATE INDEX: the runner applies a
-- role's chain in one transaction, where CONCURRENTLY is refused.
CREATE INDEX ix_work_items_status_lease_expires_at ON queue.work_items (status, lease_expires_at);
DROP INDEX queue.ix_work_items_org_id_status_lease_expires_at;
