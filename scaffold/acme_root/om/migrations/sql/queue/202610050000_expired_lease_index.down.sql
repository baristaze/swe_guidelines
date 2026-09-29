CREATE INDEX ix_work_items_org_id_status_lease_expires_at ON queue.work_items (org_id, status, lease_expires_at);
DROP INDEX queue.ix_work_items_status_lease_expires_at;
