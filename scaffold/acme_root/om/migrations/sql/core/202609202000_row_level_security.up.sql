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
-- identities is the one global table here: an identity is a person across
-- tenants, it carries no tenant column, and it gets no policy.
--
-- A table that belongs to a person inside the tenant is narrowed further
-- when the transaction names one, and not narrowed when it does not.

ALTER TABLE core.orgs ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.orgs FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_fence ON core.orgs FOR ALL
    USING (
        org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
        OR current_setting('app.org_id', true) = '00000000-0000-0000-0000-000000000000'
    )
    WITH CHECK (
        org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
        OR current_setting('app.org_id', true) = '00000000-0000-0000-0000-000000000000'
    );

ALTER TABLE core.users ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.users FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_fence ON core.users FOR ALL
    USING (
        (
            org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
            OR current_setting('app.org_id', true) = '00000000-0000-0000-0000-000000000000'
        )
        AND (
            current_setting('app.user_id', true) IS NULL
            OR current_setting('app.user_id', true) = ''
            OR identity_id = NULLIF(current_setting('app.user_id', true), '')::uuid
        )
    )
    WITH CHECK (
        (
            org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
            OR current_setting('app.org_id', true) = '00000000-0000-0000-0000-000000000000'
        )
        AND (
            current_setting('app.user_id', true) IS NULL
            OR current_setting('app.user_id', true) = ''
            OR identity_id = NULLIF(current_setting('app.user_id', true), '')::uuid
        )
    );

ALTER TABLE core.memberships ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.memberships FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_fence ON core.memberships FOR ALL
    USING (
        (
            org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
            OR current_setting('app.org_id', true) = '00000000-0000-0000-0000-000000000000'
        )
        AND (
            current_setting('app.user_id', true) IS NULL
            OR current_setting('app.user_id', true) = ''
            OR user_id = NULLIF(current_setting('app.user_id', true), '')::uuid
        )
    )
    WITH CHECK (
        (
            org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
            OR current_setting('app.org_id', true) = '00000000-0000-0000-0000-000000000000'
        )
        AND (
            current_setting('app.user_id', true) IS NULL
            OR current_setting('app.user_id', true) = ''
            OR user_id = NULLIF(current_setting('app.user_id', true), '')::uuid
        )
    );

ALTER TABLE core.sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.sessions FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_fence ON core.sessions FOR ALL
    USING (
        (
            org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
            OR current_setting('app.org_id', true) = '00000000-0000-0000-0000-000000000000'
        )
        AND (
            current_setting('app.user_id', true) IS NULL
            OR current_setting('app.user_id', true) = ''
            OR user_id = NULLIF(current_setting('app.user_id', true), '')::uuid
        )
    )
    WITH CHECK (
        (
            org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
            OR current_setting('app.org_id', true) = '00000000-0000-0000-0000-000000000000'
        )
        AND (
            current_setting('app.user_id', true) IS NULL
            OR current_setting('app.user_id', true) = ''
            OR user_id = NULLIF(current_setting('app.user_id', true), '')::uuid
        )
    );

ALTER TABLE core.api_keys ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.api_keys FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_fence ON core.api_keys FOR ALL
    USING (
        (
            org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
            OR current_setting('app.org_id', true) = '00000000-0000-0000-0000-000000000000'
        )
        AND (
            current_setting('app.user_id', true) IS NULL
            OR current_setting('app.user_id', true) = ''
            OR user_id = NULLIF(current_setting('app.user_id', true), '')::uuid
        )
    )
    WITH CHECK (
        (
            org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
            OR current_setting('app.org_id', true) = '00000000-0000-0000-0000-000000000000'
        )
        AND (
            current_setting('app.user_id', true) IS NULL
            OR current_setting('app.user_id', true) = ''
            OR user_id = NULLIF(current_setting('app.user_id', true), '')::uuid
        )
    );

ALTER TABLE core.socket_tickets ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.socket_tickets FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_fence ON core.socket_tickets FOR ALL
    USING (
        (
            org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
            OR current_setting('app.org_id', true) = '00000000-0000-0000-0000-000000000000'
        )
        AND (
            current_setting('app.user_id', true) IS NULL
            OR current_setting('app.user_id', true) = ''
            OR user_id = NULLIF(current_setting('app.user_id', true), '')::uuid
        )
    )
    WITH CHECK (
        (
            org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
            OR current_setting('app.org_id', true) = '00000000-0000-0000-0000-000000000000'
        )
        AND (
            current_setting('app.user_id', true) IS NULL
            OR current_setting('app.user_id', true) = ''
            OR user_id = NULLIF(current_setting('app.user_id', true), '')::uuid
        )
    );

ALTER TABLE core.tasks ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.tasks FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_fence ON core.tasks FOR ALL
    USING (
        org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
        OR current_setting('app.org_id', true) = '00000000-0000-0000-0000-000000000000'
    )
    WITH CHECK (
        org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
        OR current_setting('app.org_id', true) = '00000000-0000-0000-0000-000000000000'
    );

ALTER TABLE core.idempotency_records ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.idempotency_records FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_fence ON core.idempotency_records FOR ALL
    USING (
        (
            org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
            OR current_setting('app.org_id', true) = '00000000-0000-0000-0000-000000000000'
        )
        AND (
            current_setting('app.user_id', true) IS NULL
            OR current_setting('app.user_id', true) = ''
            OR user_id = NULLIF(current_setting('app.user_id', true), '')::uuid
        )
    )
    WITH CHECK (
        (
            org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
            OR current_setting('app.org_id', true) = '00000000-0000-0000-0000-000000000000'
        )
        AND (
            current_setting('app.user_id', true) IS NULL
            OR current_setting('app.user_id', true) = ''
            OR user_id = NULLIF(current_setting('app.user_id', true), '')::uuid
        )
    );

ALTER TABLE core.outbox_rows ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.outbox_rows FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_fence ON core.outbox_rows FOR ALL
    USING (
        org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
        OR current_setting('app.org_id', true) = '00000000-0000-0000-0000-000000000000'
    )
    WITH CHECK (
        org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
        OR current_setting('app.org_id', true) = '00000000-0000-0000-0000-000000000000'
    );
