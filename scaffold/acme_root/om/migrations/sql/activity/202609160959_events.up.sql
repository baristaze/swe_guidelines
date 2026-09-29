-- The event stream: append-only, one sequence per tenant, read by seq on a
-- reconnect and by id as a feed. org_id leads both compound indexes.

CREATE TABLE activity.events (
    id uuid NOT NULL,
    org_id uuid NOT NULL,
    seq integer NOT NULL,
    entity text NOT NULL,
    entity_id uuid NOT NULL,
    action text NOT NULL,
    produced_at timestamptz NOT NULL,
    idempotency_key uuid NOT NULL,
    actor_id uuid NOT NULL,
    CONSTRAINT pk_events PRIMARY KEY (id)
);
CREATE UNIQUE INDEX uq_events_org_id_seq ON activity.events (org_id, seq);
CREATE INDEX ix_events_org_id_id ON activity.events (org_id, id);
