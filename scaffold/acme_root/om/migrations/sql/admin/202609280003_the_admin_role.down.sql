-- Takes the admin role back to empty: the grants, then the size tally.

ALTER DEFAULT PRIVILEGES FOR ROLE acme_migration IN SCHEMA admin
    REVOKE USAGE, SELECT ON SEQUENCES FROM acme_runtime, acme_system;
ALTER DEFAULT PRIVILEGES FOR ROLE acme_migration IN SCHEMA admin
    REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM acme_runtime, acme_system;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA admin FROM acme_runtime, acme_system;
REVOKE ALL ON ALL TABLES IN SCHEMA admin FROM acme_runtime, acme_system;
REVOKE USAGE ON SCHEMA admin FROM acme_runtime, acme_system;

DROP TABLE admin.platform_sizes;
