-- The transactional outbox: a handoff that follows a core write lands in the
-- same commit as the core row, is relayed at once, and is swept after a
-- crash. The sweep reads pending rows across tenants oldest first and purges
-- done ones by time, both over (done_at, id).

CREATE TABLE core.outbox_rows (
    id uuid NOT NULL,
    org_id uuid NOT NULL,
    created_at timestamptz NOT NULL,
    kind text NOT NULL,
    target_id uuid NOT NULL,
    payload jsonb NOT NULL,
    actor_id uuid NOT NULL,
    request_id uuid NOT NULL,
    app text NOT NULL,
    done_at timestamptz NULL,
    CONSTRAINT pk_outbox_rows PRIMARY KEY (id)
);
CREATE INDEX ix_outbox_rows_org_id ON core.outbox_rows (org_id);
CREATE INDEX ix_outbox_rows_done_at_id ON core.outbox_rows (done_at, id);
