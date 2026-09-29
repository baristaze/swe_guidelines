DROP POLICY tenant_fence ON core.orchestrations;
DROP TABLE core.orchestrations;
ALTER TABLE core.tasks DROP COLUMN archived_at;
