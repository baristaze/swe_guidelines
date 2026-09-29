-- The admin role, whole: the operator plane's own tables. The one table is
-- the platform's size as the sweep last counted it: one row, whose id is the
-- empty UUID. Only that count writes the role, so the operator plane's read
-- of it never counts a role the application writes to (ADR 0074). It holds
-- no tenant's row, so it carries no fence.

CREATE TABLE admin.platform_sizes (
    id uuid NOT NULL,
    tenants bigint NOT NULL,
    users bigint NOT NULL,
    events_last_24h bigint NOT NULL,
    since timestamptz NOT NULL,
    counted_at timestamptz NOT NULL,
    CONSTRAINT pk_platform_sizes PRIMARY KEY (id)
);

-- The serving logins hold DML and nothing else, now and on every table to
-- come; the migration bookkeeping is the migration login's alone.

GRANT USAGE ON SCHEMA admin TO acme_runtime, acme_system;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA admin TO acme_runtime, acme_system;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA admin TO acme_runtime, acme_system;
ALTER DEFAULT PRIVILEGES FOR ROLE acme_migration IN SCHEMA admin
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO acme_runtime, acme_system;
ALTER DEFAULT PRIVILEGES FOR ROLE acme_migration IN SCHEMA admin
    GRANT USAGE, SELECT ON SEQUENCES TO acme_runtime, acme_system;
REVOKE ALL ON admin.alembic_version FROM acme_runtime, acme_system;
