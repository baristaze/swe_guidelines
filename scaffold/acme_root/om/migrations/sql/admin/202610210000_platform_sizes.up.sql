-- The platform's size as the maintenance worker's sweep last counted it: the
-- live orgs and users, and the tasks created and events produced in the day
-- before `counted_at`. One row, the platform's own, keyed by the empty uuid,
-- and rewritten by each newer count. The operator plane reads it and never
-- counts, so no request scans a role the application writes to (ADR 0074).
-- A global row: no tenant, no policy. The runtime and the system logins hold
-- DML on it by the default privileges `migrate ensure-logins` sets on every
-- role schema, which runs before this.

CREATE TABLE admin.platform_sizes (
    id uuid NOT NULL,
    tenants bigint NOT NULL,
    users bigint NOT NULL,
    tasks_last_24h bigint NOT NULL,
    events_last_24h bigint NOT NULL,
    since timestamptz NOT NULL,
    counted_at timestamptz NOT NULL,
    CONSTRAINT pk_platform_sizes PRIMARY KEY (id)
);
