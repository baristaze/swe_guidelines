-- The idempotency swimlane: the durable outcome of a request the caller may
-- retry. org_id leads the unique compound index, so it gets no index of its own.

CREATE TABLE core.idempotency_records (
    id uuid NOT NULL,
    org_id uuid NOT NULL,
    user_id uuid NOT NULL,
    key text NOT NULL,
    request_digest text NOT NULL,
    status integer NULL,
    body text NULL,
    created_at timestamptz NOT NULL,
    CONSTRAINT pk_idempotency_records PRIMARY KEY (id)
);
CREATE UNIQUE INDEX uq_idempotency_records_org_id_user_id_key ON core.idempotency_records (org_id, user_id, key);
