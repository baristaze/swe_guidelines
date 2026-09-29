-- Socket tickets: a single-use credential standing for a session or an api
-- key. Redeeming one is a conditional update on its hash, so the row, not a
-- cache entry, decides who was first.

CREATE TABLE core.socket_tickets (
    id uuid NOT NULL,
    org_id uuid NOT NULL,
    created_at timestamptz NOT NULL,
    user_id uuid NOT NULL,
    ticket_hash text NOT NULL,
    credential_kind text NOT NULL,
    credential_id uuid NOT NULL,
    expires_at timestamptz NOT NULL,
    redeemed_at timestamptz NULL,
    CONSTRAINT pk_socket_tickets PRIMARY KEY (id)
);
CREATE INDEX ix_socket_tickets_org_id ON core.socket_tickets (org_id);
CREATE UNIQUE INDEX uq_socket_tickets_ticket_hash ON core.socket_tickets (ticket_hash);
