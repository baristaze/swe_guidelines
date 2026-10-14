-- The queue role, whole: durable background work, one row per item, with
-- its own claim (a lease and a token) on the row, and each tenant's own cap
-- on a lane.
--
-- The key is unique per tenant: a producer that asks twice meets the item
-- it asked for. The claim walks the ready items of a lane in order; the
-- sweep requeues the leases that expired and purges the settled.

CREATE TABLE queue.work_items (
    id uuid NOT NULL,
    org_id uuid NOT NULL,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    created_by uuid NOT NULL,
    updated_by uuid NOT NULL,
    kind text NOT NULL,
    target_id uuid NOT NULL,
    idempotency_key uuid NOT NULL,
    request_id uuid NOT NULL,
    traceparent text NULL,
    payload jsonb NOT NULL,
    lane text NOT NULL,
    status text NOT NULL,
    available_at timestamptz NOT NULL,
    claimed_by text NULL,
    claim_token uuid NULL,
    lease_expires_at timestamptz NULL,
    attempts integer NOT NULL,
    max_attempts integer NOT NULL,
    last_error text NULL,
    CONSTRAINT pk_work_items PRIMARY KEY (id)
);
CREATE UNIQUE INDEX uq_work_items_org_id_idempotency_key
    ON queue.work_items (org_id, idempotency_key);
CREATE INDEX ix_work_items_lane_status_available_at_id
    ON queue.work_items (lane, status, available_at, id);
CREATE INDEX ix_work_items_available_at_queued ON queue.work_items (available_at)
    WHERE status = 'queued';
CREATE INDEX ix_work_items_status_lease_expires_at ON queue.work_items (status, lease_expires_at);
CREATE INDEX ix_work_items_status_updated_at ON queue.work_items (status, updated_at);

-- The queue's fence, as one policy per login (ADR 0044):
--
-- - tenant_fence, TO acme_runtime: the rows of the tenant the transaction
--   names, and nothing else. It carries no system-scope clause, so the
--   runtime login naming the system scope reads nothing.
-- - system_fence, TO acme_system: every row, when the transaction names the
--   system scope, the empty UUID; nothing when it names none.
--
-- A login no policy names reads nothing, since FORCE holds the owner too: the
-- migration login and the master read no work item under any setting. The
-- system login's claim sees one plain predicate on the setting, so it walks
-- the claim's index in order and stops at the first free row.

ALTER TABLE queue.work_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE queue.work_items FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_fence ON queue.work_items FOR ALL TO acme_runtime
    USING (org_id = NULLIF(current_setting('app.org_id', true), '')::uuid)
    WITH CHECK (org_id = NULLIF(current_setting('app.org_id', true), '')::uuid);
CREATE POLICY system_fence ON queue.work_items FOR ALL TO acme_system
    USING (current_setting('app.org_id', true) = '00000000-0000-0000-0000-000000000000')
    WITH CHECK (current_setting('app.org_id', true) = '00000000-0000-0000-0000-000000000000');

-- A tenant's own cap on a lane: one row per tenant and lane, which the
-- claim joins to its count of the lane's claimed items, in place of the
-- lane's cap for that tenant.

CREATE TABLE queue.tenant_caps (
    id uuid NOT NULL,
    org_id uuid NOT NULL,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    created_by uuid NOT NULL,
    updated_by uuid NOT NULL,
    lane text NOT NULL,
    cap integer NOT NULL,
    CONSTRAINT pk_tenant_caps PRIMARY KEY (id)
);
CREATE UNIQUE INDEX uq_tenant_caps_org_id_lane ON queue.tenant_caps (org_id, lane);

-- The second fence, as on every tenant table (ADR 0016): the transaction's
-- own tenant, or the system scope to the system login alone. An operator's
-- write reaches the one tenant it names; the claim reads the caps of the
-- lane it claims from under the system scope.

ALTER TABLE queue.tenant_caps ENABLE ROW LEVEL SECURITY;
ALTER TABLE queue.tenant_caps FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_fence ON queue.tenant_caps
    USING (
        org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
        OR (
            current_setting('app.org_id', true) = '00000000-0000-0000-0000-000000000000'
            AND current_user = 'acme_system'
        )
    )
    WITH CHECK (
        org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
        OR (
            current_setting('app.org_id', true) = '00000000-0000-0000-0000-000000000000'
            AND current_user = 'acme_system'
        )
    );

-- The serving logins hold DML and nothing else, on every table there is and
-- every table to come; the migration bookkeeping is the migration login's
-- alone.

GRANT USAGE ON SCHEMA queue TO acme_runtime, acme_system;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA queue TO acme_runtime, acme_system;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA queue TO acme_runtime, acme_system;
ALTER DEFAULT PRIVILEGES FOR ROLE acme_migration IN SCHEMA queue
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO acme_runtime, acme_system;
ALTER DEFAULT PRIVILEGES FOR ROLE acme_migration IN SCHEMA queue
    GRANT USAGE, SELECT ON SEQUENCES TO acme_runtime, acme_system;
REVOKE ALL ON queue.alembic_version FROM acme_runtime, acme_system;
