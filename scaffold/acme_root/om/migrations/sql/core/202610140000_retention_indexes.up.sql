-- The sweep purges each namespace's rows past their retention once a pass,
-- across tenants, in the system scope. Each purge statement reads its rows
-- through an index that leads with the column its retention is counted on,
-- so a batch reads the rows it deletes and no tenant is visited to find
-- them. Plain CREATE INDEX: the runner applies a role's chain in one
-- transaction, where CONCURRENTLY is refused. Every build comes before
-- every drop.
--
-- A partial predicate names no bound value, only IS NULL and IS NOT NULL,
-- so a generic plan can use it too. Where a status picks the rows (the
-- pending uploads, the settled orchestrations), the status leads and the
-- retention column follows.

-- Tasks and files: the deleted ones by their delete, and the uploads never
-- confirmed by their birth.
CREATE INDEX ix_tasks_deleted_at ON core.tasks (deleted_at) WHERE deleted_at IS NOT NULL;
CREATE INDEX ix_files_deleted_at ON core.files (deleted_at) WHERE deleted_at IS NOT NULL;
CREATE INDEX ix_files_status_created_at ON core.files (status, created_at) WHERE deleted_at IS NULL;

-- Tenancy: removed users and ended memberships, revoked and expired keys,
-- expired sessions and tickets, expired and closed invitations.
CREATE INDEX ix_users_deleted_at ON core.users (deleted_at) WHERE deleted_at IS NOT NULL;
CREATE INDEX ix_memberships_deleted_at ON core.memberships (deleted_at) WHERE deleted_at IS NOT NULL;
CREATE INDEX ix_api_keys_deleted_at ON core.api_keys (deleted_at) WHERE deleted_at IS NOT NULL;
CREATE INDEX ix_api_keys_expires_at ON core.api_keys (expires_at);
CREATE INDEX ix_sessions_expires_at ON core.sessions (expires_at);
CREATE INDEX ix_socket_tickets_expires_at ON core.socket_tickets (expires_at);
CREATE INDEX ix_invitations_expires_at ON core.invitations (expires_at);
CREATE INDEX ix_invitations_updated_at ON core.invitations (updated_at);

-- Idempotency records by their birth, and the pending markers by their
-- attempt. An attempt token is minted once, so the second also serves the
-- re-mint's fence, which reads one marker by its attempt, and replaces the
-- index that led that read with org_id.
CREATE INDEX ix_idempotency_records_created_at ON core.idempotency_records (created_at);
CREATE INDEX ix_idempotency_records_attempt_id ON core.idempotency_records (attempt_id) WHERE status IS NULL;

-- The payment processor's delivery marks by their birth.
CREATE INDEX ix_billing_deliveries_created_at ON core.billing_deliveries (created_at);

-- Slack: uninstalled installations, spent install states, and posts.
CREATE INDEX ix_slack_installations_deleted_at ON core.slack_installations (deleted_at) WHERE deleted_at IS NOT NULL;
CREATE INDEX ix_slack_install_states_expires_at ON core.slack_install_states (expires_at);
CREATE INDEX ix_slack_install_states_redeemed_at ON core.slack_install_states (redeemed_at) WHERE redeemed_at IS NOT NULL;
CREATE INDEX ix_slack_posts_created_at ON core.slack_posts (created_at);

-- Orchestrations that settled, by their last change.
CREATE INDEX ix_orchestrations_status_updated_at ON core.orchestrations (status, updated_at);

-- The drops: indexes only the per-tenant purges read, and the fence's, which
-- the attempt's index above serves. A tenant's own rows stay reachable by
-- another index that org_id leads.
DROP INDEX core.ix_tasks_org_id_deleted_at;
DROP INDEX core.ix_idempotency_records_org_id_created_at;
DROP INDEX core.ix_idempotency_records_org_id_attempt_id;
DROP INDEX core.ix_slack_posts_org_id_created_at;
