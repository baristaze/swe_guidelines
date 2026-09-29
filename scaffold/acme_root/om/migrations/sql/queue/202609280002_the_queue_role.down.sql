-- Takes the queue role back to empty: the grants, then the work items with
-- their two policies.

ALTER DEFAULT PRIVILEGES FOR ROLE acme_migration IN SCHEMA queue
    REVOKE USAGE, SELECT ON SEQUENCES FROM acme_runtime, acme_system;
ALTER DEFAULT PRIVILEGES FOR ROLE acme_migration IN SCHEMA queue
    REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM acme_runtime, acme_system;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA queue FROM acme_runtime, acme_system;
REVOKE ALL ON ALL TABLES IN SCHEMA queue FROM acme_runtime, acme_system;
REVOKE USAGE ON SCHEMA queue FROM acme_runtime, acme_system;

DROP TABLE queue.work_items;
