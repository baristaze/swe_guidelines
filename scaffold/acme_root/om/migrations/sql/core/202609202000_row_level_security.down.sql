-- Reverses the second fence: the policy goes, the table stops forcing and
-- stops enforcing row-level security, and the predicate in the query is the
-- only fence again.

DROP POLICY tenant_fence ON core.orgs;
ALTER TABLE core.orgs NO FORCE ROW LEVEL SECURITY;
ALTER TABLE core.orgs DISABLE ROW LEVEL SECURITY;

DROP POLICY tenant_fence ON core.users;
ALTER TABLE core.users NO FORCE ROW LEVEL SECURITY;
ALTER TABLE core.users DISABLE ROW LEVEL SECURITY;

DROP POLICY tenant_fence ON core.memberships;
ALTER TABLE core.memberships NO FORCE ROW LEVEL SECURITY;
ALTER TABLE core.memberships DISABLE ROW LEVEL SECURITY;

DROP POLICY tenant_fence ON core.sessions;
ALTER TABLE core.sessions NO FORCE ROW LEVEL SECURITY;
ALTER TABLE core.sessions DISABLE ROW LEVEL SECURITY;

DROP POLICY tenant_fence ON core.api_keys;
ALTER TABLE core.api_keys NO FORCE ROW LEVEL SECURITY;
ALTER TABLE core.api_keys DISABLE ROW LEVEL SECURITY;

DROP POLICY tenant_fence ON core.socket_tickets;
ALTER TABLE core.socket_tickets NO FORCE ROW LEVEL SECURITY;
ALTER TABLE core.socket_tickets DISABLE ROW LEVEL SECURITY;

DROP POLICY tenant_fence ON core.tasks;
ALTER TABLE core.tasks NO FORCE ROW LEVEL SECURITY;
ALTER TABLE core.tasks DISABLE ROW LEVEL SECURITY;

DROP POLICY tenant_fence ON core.idempotency_records;
ALTER TABLE core.idempotency_records NO FORCE ROW LEVEL SECURITY;
ALTER TABLE core.idempotency_records DISABLE ROW LEVEL SECURITY;

DROP POLICY tenant_fence ON core.outbox_rows;
ALTER TABLE core.outbox_rows NO FORCE ROW LEVEL SECURITY;
ALTER TABLE core.outbox_rows DISABLE ROW LEVEL SECURITY;
