-- The cursor row per tenant. The append takes the next seq as `head + 1`
-- under the row's lock inside its own transaction, and `head` is the tenant's
-- head seq the pong carries. Backfilled from the stream so a tenant with
-- events continues where it stands.

CREATE TABLE activity.event_cursors (
    org_id uuid NOT NULL,
    head bigint NOT NULL,
    CONSTRAINT pk_event_cursors PRIMARY KEY (org_id)
);
INSERT INTO activity.event_cursors (org_id, head)
SELECT org_id, max(seq) FROM activity.events GROUP BY org_id;
