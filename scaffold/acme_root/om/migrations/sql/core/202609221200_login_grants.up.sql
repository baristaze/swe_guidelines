-- The runtime and the system logins hold DML on every table of the role and
-- nothing else: no table is theirs, so neither can drop a policy, turn FORCE
-- off, or alter a table. Every table the migration login creates from here on
-- grants the same, by default privilege. The migration bookkeeping is the
-- migration login's alone. `migrate ensure-logins` makes the logins and runs
-- the same grants, so it runs before this.

GRANT USAGE ON SCHEMA core TO acme_runtime, acme_system;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA core TO acme_runtime, acme_system;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA core TO acme_runtime, acme_system;
ALTER DEFAULT PRIVILEGES FOR ROLE acme_migration IN SCHEMA core
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO acme_runtime, acme_system;
ALTER DEFAULT PRIVILEGES FOR ROLE acme_migration IN SCHEMA core
    GRANT USAGE, SELECT ON SEQUENCES TO acme_runtime, acme_system;
REVOKE ALL ON core.alembic_version FROM acme_runtime, acme_system;
