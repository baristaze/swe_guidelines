-- Long-running orchestrations, and the archive of old done tasks. Expand
-- only: a table and a nullable column, which the release before this one
-- never names.
--
-- An orchestration is a durable record with a status and a cursor, advanced
-- one step at a time by whichever worker holds its work item: the import of
-- tasks from a CSV file, and the daily cleanup of old done tasks. A record
-- kept per period (the cleanup's day) is one per org, kind, and period. It
-- belongs to the tenant and carries the tenant fence every tenant table does.
--
-- A task the cleanup archived carries when; it leaves the done list and can
-- be read and restored.

ALTER TABLE core.tasks ADD COLUMN archived_at timestamptz NULL;

CREATE TABLE core.orchestrations (
    id uuid NOT NULL,
    org_id uuid NOT NULL,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    created_by uuid NOT NULL,
    updated_by uuid NOT NULL,
    kind text NOT NULL,
    input jsonb NOT NULL,
    period text NULL,
    status text NOT NULL,
    cursor integer NOT NULL,
    total integer NULL,
    applied integer NOT NULL,
    skipped integer NOT NULL,
    row_errors jsonb NOT NULL,
    park_reason text NULL,
    fail_reason text NULL,
    fail_detail text NULL,
    finished_at timestamptz NULL,
    version integer NOT NULL,
    CONSTRAINT pk_orchestrations PRIMARY KEY (id)
);
CREATE UNIQUE INDEX uq_orchestrations_org_id_kind_period
    ON core.orchestrations (org_id, kind, period) WHERE period IS NOT NULL;
CREATE INDEX ix_orchestrations_org_id_kind_id ON core.orchestrations (org_id, kind, id);
CREATE INDEX ix_orchestrations_org_id_status_updated_at
    ON core.orchestrations (org_id, status, updated_at);

ALTER TABLE core.orchestrations ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.orchestrations FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_fence ON core.orchestrations FOR ALL
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
