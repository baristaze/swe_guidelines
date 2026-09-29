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
-- The queue's one table belongs to a tenant. The claim runs in the system
-- scope, since a worker sweeps every tenant's lane.

ALTER TABLE queue.work_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE queue.work_items FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_fence ON queue.work_items FOR ALL
    USING (
        org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
        OR current_setting('app.org_id', true) = '00000000-0000-0000-0000-000000000000'
    )
    WITH CHECK (
        org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
        OR current_setting('app.org_id', true) = '00000000-0000-0000-0000-000000000000'
    );
