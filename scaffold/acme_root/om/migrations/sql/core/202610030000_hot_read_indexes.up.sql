-- Indexes that fit the reads the task lists, the daily cleanup, and the sweep
-- send, and three indexes no statement needs. Plain CREATE INDEX: the runner
-- applies a role's chain in one transaction, where CONCURRENTLY is refused.
-- Every build comes before every drop, so the tables a drop locks stay locked
-- only for the rest of the commit.
--
-- The `mine` scope: the caller's tasks are the ones assigned to them, and the
-- unassigned ones they made. Each arm of that OR has its own index, and the
-- planner joins them in a BitmapOr, so a member with few tasks reads only
-- theirs instead of every open or done task of the tenant. The predicates
-- name no bound value, only IS NULL, so a generic plan can use them too.
CREATE INDEX ix_tasks_org_id_assignee_id_status ON core.tasks (org_id, assignee_id, status) WHERE deleted_at IS NULL;
CREATE INDEX ix_tasks_org_id_created_by_status ON core.tasks (org_id, created_by, status) WHERE assignee_id IS NULL AND deleted_at IS NULL;

-- The done list and the archive, one index per shelf. The daily cleanup's
-- read of the archivable tasks walks only the unarchived ones, however large
-- the archive grows. The predicates leave `status` out: the storage binds it
-- as a parameter, and a generic plan cannot prove a partial predicate on one.
CREATE INDEX ix_tasks_org_id_status_updated_at_id_unarchived ON core.tasks (org_id, status, updated_at, id) WHERE archived_at IS NULL AND deleted_at IS NULL;
CREATE INDEX ix_tasks_org_id_status_updated_at_id_archived ON core.tasks (org_id, status, updated_at, id) WHERE archived_at IS NOT NULL AND deleted_at IS NULL;

-- The pending markers (no status yet) by their attempt. The idempotency
-- purge reads a tenant's abandoned attempts through it, and the re-mint's
-- fence reads the one marker its attempt holds. It replaces the fence's
-- (org_id, target_id) index, which held every marker, finished ones too.
CREATE INDEX ix_idempotency_records_org_id_attempt_id ON core.idempotency_records (org_id, attempt_id) WHERE status IS NULL;

-- The invitations purge and the tenant purge read one tenant's invitations.
CREATE INDEX ix_invitations_org_id_expires_at ON core.invitations (org_id, expires_at);

-- The Slack purge reads a tenant's uninstalled installations, and the tenant
-- purge all of them. Both unique indexes hold only the living ones.
CREATE INDEX ix_slack_installations_org_id_deleted_at ON core.slack_installations (org_id, deleted_at);

-- The drops.
DROP INDEX core.ix_tasks_org_id_status_updated_at_id;
DROP INDEX core.ix_idempotency_records_org_id_target_id;
-- No statement reads the outbox by tenant: every read is by id or by
-- done_at and failed_at, across tenants.
DROP INDEX core.ix_outbox_rows_org_id;
-- The digest is computed from the address, so the two enforce one rule. The
-- digest's index is the one the sign-in reads by, so it stays.
DROP INDEX core.uq_identities_email;
