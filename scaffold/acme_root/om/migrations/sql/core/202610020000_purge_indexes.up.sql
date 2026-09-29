-- An index under every purge statement the sweep runs, so each batch reads
-- the rows it deletes and not the whole tenant. Plain CREATE INDEX: the
-- runner applies a role's chain in one transaction, where CONCURRENTLY is
-- refused.
--
-- The outbox purge deletes done rows and failed rows in two statements. The
-- done ones walk ix_outbox_rows_done_at_id; the failed ones walk this
-- partial index, which holds only the dead letters.
CREATE INDEX ix_outbox_rows_failed_at ON core.outbox_rows (failed_at) WHERE failed_at IS NOT NULL;

-- A task purge reads the tenant's deleted tasks past the retention; most
-- tasks are never deleted, so the index holds only those that are.
CREATE INDEX ix_tasks_org_id_deleted_at ON core.tasks (org_id, deleted_at) WHERE deleted_at IS NOT NULL;

-- Sessions and socket tickets go by their expiry. Each index leads with
-- org_id, so it replaces the plain org_id index as well.
CREATE INDEX ix_sessions_org_id_expires_at ON core.sessions (org_id, expires_at);
DROP INDEX core.ix_sessions_org_id;
CREATE INDEX ix_socket_tickets_org_id_expires_at ON core.socket_tickets (org_id, expires_at);
DROP INDEX core.ix_socket_tickets_org_id;

-- Idempotency records and Slack posts go by their birth.
CREATE INDEX ix_idempotency_records_org_id_created_at ON core.idempotency_records (org_id, created_at);
CREATE INDEX ix_slack_posts_org_id_created_at ON core.slack_posts (org_id, created_at);
