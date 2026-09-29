-- The work queue: hot and tiny, in its own role so a claim never competes
-- with an analytical scan.

CREATE TABLE queue.work_items (
    id uuid NOT NULL,
    org_id uuid NOT NULL,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    created_by uuid NOT NULL,
    kind text NOT NULL,
    target_id uuid NOT NULL,
    idempotency_key uuid NOT NULL,
    payload jsonb NOT NULL,
    queue text NOT NULL,
    status text NOT NULL,
    available_at timestamptz NOT NULL,
    claimed_by text NULL,
    lease_expires_at timestamptz NULL,
    attempts integer NOT NULL,
    max_attempts integer NOT NULL,
    last_error text NULL,
    CONSTRAINT pk_work_items PRIMARY KEY (id)
);
CREATE INDEX ix_work_items_org_id ON queue.work_items (org_id);
CREATE UNIQUE INDEX uq_work_items_idempotency_key ON queue.work_items (idempotency_key);
CREATE INDEX ix_work_items_queue_status_available_at ON queue.work_items (queue, status, available_at);
