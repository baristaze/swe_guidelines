-- The activity role, whole: the event stream behind every realtime push and
-- every audit entry, and one cursor row per tenant.
--
-- The append takes the next seq as `head + 1` under the cursor row's lock, in
-- its own transaction, so a tenant's stream is gapless. The floor is the
-- highest seq the trim has removed, 0 while it has removed none; a read that
-- starts below it is refused, because the events it asks for are gone
-- (ADR 0040).

CREATE TABLE activity.events (
    id uuid NOT NULL,
    org_id uuid NOT NULL,
    seq bigint NOT NULL,
    kind text NOT NULL,
    target_id uuid NOT NULL,
    produced_at timestamptz NOT NULL,
    actor_id uuid NOT NULL,
    request_id uuid NOT NULL,
    app text NOT NULL,
    payload jsonb NOT NULL,
    CONSTRAINT pk_events PRIMARY KEY (id)
);
CREATE UNIQUE INDEX uq_events_org_id_seq ON activity.events (org_id, seq);
-- The operator's size counts the events produced in the last day, across
-- every tenant, and the trim reads the oldest: a b-tree on the time.
CREATE INDEX ix_events_produced_at ON activity.events (produced_at);

CREATE TABLE activity.event_cursors (
    org_id uuid NOT NULL,
    head bigint NOT NULL,
    floor bigint NOT NULL DEFAULT 0,
    CONSTRAINT pk_event_cursors PRIMARY KEY (org_id)
);

-- The second fence: the transaction's own tenant, or the system scope to the
-- system login alone (ADR 0016).

ALTER TABLE activity.events ENABLE ROW LEVEL SECURITY;
ALTER TABLE activity.events FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_fence ON activity.events
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

ALTER TABLE activity.event_cursors ENABLE ROW LEVEL SECURITY;
ALTER TABLE activity.event_cursors FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_fence ON activity.event_cursors
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

-- The serving logins hold DML and nothing else, now and on every table to
-- come; the migration bookkeeping is the migration login's alone.

GRANT USAGE ON SCHEMA activity TO acme_runtime, acme_system;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA activity TO acme_runtime, acme_system;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA activity TO acme_runtime, acme_system;
ALTER DEFAULT PRIVILEGES FOR ROLE acme_migration IN SCHEMA activity
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO acme_runtime, acme_system;
ALTER DEFAULT PRIVILEGES FOR ROLE acme_migration IN SCHEMA activity
    GRANT USAGE, SELECT ON SEQUENCES TO acme_runtime, acme_system;
REVOKE ALL ON activity.alembic_version FROM acme_runtime, acme_system;
