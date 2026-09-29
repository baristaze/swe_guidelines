DROP INDEX core.ix_slack_posts_org_id_created_at;
DROP INDEX core.ix_idempotency_records_org_id_created_at;
CREATE INDEX ix_socket_tickets_org_id ON core.socket_tickets (org_id);
DROP INDEX core.ix_socket_tickets_org_id_expires_at;
CREATE INDEX ix_sessions_org_id ON core.sessions (org_id);
DROP INDEX core.ix_sessions_org_id_expires_at;
DROP INDEX core.ix_tasks_org_id_deleted_at;
DROP INDEX core.ix_outbox_rows_failed_at;
