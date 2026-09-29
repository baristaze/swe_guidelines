-- The second fence. Every tenant table carries a policy that holds the rows
-- of one tenant, and the tenant comes from a transaction setting the storage
-- funnel writes once per transaction. The predicate in the query is still the
-- fence the business layer relies on; this is what catches the predicate that
-- went missing.
--
-- FORCE holds the owner too, which the application login is, so the policy is
-- not a drawing behind a role that walks past it.
--
-- A transaction that names no tenant reads nothing and writes nothing: fail
-- closed. A setting that was never written reads as NULL, and every
-- comparison to NULL is false. A setting written with set_config(..., true)
-- in an earlier transaction on the same connection does not go back to NULL
-- when that transaction ends; it goes back to the empty string, which casts
-- to no uuid at all. NULLIF makes the two the same answer, which is the
-- answer a fence gives when nobody says whose rows these are.
--
-- The system scope is the one deliberate bypass, spelled in every policy as
-- the empty UUID, so every place it holds can be found by reading the chain.
--
-- Both tables here belong to a tenant and to nobody in it.

ALTER TABLE activity.events ENABLE ROW LEVEL SECURITY;
ALTER TABLE activity.events FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_fence ON activity.events FOR ALL
    USING (
        org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
        OR current_setting('app.org_id', true) = '00000000-0000-0000-0000-000000000000'
    )
    WITH CHECK (
        org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
        OR current_setting('app.org_id', true) = '00000000-0000-0000-0000-000000000000'
    );

ALTER TABLE activity.event_cursors ENABLE ROW LEVEL SECURITY;
ALTER TABLE activity.event_cursors FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_fence ON activity.event_cursors FOR ALL
    USING (
        org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
        OR current_setting('app.org_id', true) = '00000000-0000-0000-0000-000000000000'
    )
    WITH CHECK (
        org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
        OR current_setting('app.org_id', true) = '00000000-0000-0000-0000-000000000000'
    );
