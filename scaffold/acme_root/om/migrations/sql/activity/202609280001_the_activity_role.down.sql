-- Takes the activity role back to empty: the grants, then the stream and its
-- cursors with their policies.

ALTER DEFAULT PRIVILEGES FOR ROLE acme_migration IN SCHEMA activity
    REVOKE USAGE, SELECT ON SEQUENCES FROM acme_runtime, acme_system;
ALTER DEFAULT PRIVILEGES FOR ROLE acme_migration IN SCHEMA activity
    REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM acme_runtime, acme_system;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA activity FROM acme_runtime, acme_system;
REVOKE ALL ON ALL TABLES IN SCHEMA activity FROM acme_runtime, acme_system;
REVOKE USAGE ON SCHEMA activity FROM acme_runtime, acme_system;

DROP TABLE activity.event_cursors;
DROP TABLE activity.events;
