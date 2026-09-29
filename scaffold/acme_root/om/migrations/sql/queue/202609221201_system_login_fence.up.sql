-- The system scope belongs to the system login. Every policy spelled the
-- system scope as the empty UUID alone, and any session may write a setting,
-- so a statement injected into a request could name it and read across
-- tenants. The clause now also names the login: the runtime login naming the
-- system scope reads nothing, and only the listed system-scope methods, on
-- the system login's pool, read across tenants.
--
-- `acme` is the login every process used before the three logins existed.
-- It stays in the clause for one release, so the tasks of the release before
-- keep serving while this one rolls out beside them; the release after drops
-- it.
--
-- ALTER, not DROP and CREATE: each table keeps its one policy under its name.

ALTER POLICY tenant_fence ON queue.work_items
    USING (
        org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
        OR (
            current_setting('app.org_id', true) = '00000000-0000-0000-0000-000000000000'
            AND current_user IN ('acme_system', 'acme')
        )
    )
    WITH CHECK (
        org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
        OR (
            current_setting('app.org_id', true) = '00000000-0000-0000-0000-000000000000'
            AND current_user IN ('acme_system', 'acme')
        )
    );
