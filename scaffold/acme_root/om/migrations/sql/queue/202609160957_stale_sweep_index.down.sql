CREATE INDEX ix_work_items_org_id ON queue.work_items (org_id);
DROP INDEX queue.ix_work_items_org_id_status_lease_expires_at;
