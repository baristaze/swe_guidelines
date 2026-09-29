-- The per-tenant purge runs every 30 seconds and reads what no index serves.
-- `uq_users_org_id_identity_id_live` and `uq_memberships_org_id_user_id` are
-- unique among the living, `WHERE deleted_at IS NULL`; the sweep asks for
-- `org_id = X AND deleted_at < Y`, and for `org_id = X` alone under a tenant
-- past its retention. Those are the rows the partial indexes leave out, so
-- each table gets a plain (org_id, deleted_at) index, which serves both.

CREATE INDEX ix_users_org_id_deleted_at ON core.users (org_id, deleted_at);
CREATE INDEX ix_memberships_org_id_deleted_at ON core.memberships (org_id, deleted_at);
