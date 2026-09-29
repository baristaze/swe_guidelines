-- Puts back the system-scope clause without the login: the empty UUID alone.

ALTER POLICY tenant_fence ON core.orgs
    USING (
        org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
        OR current_setting('app.org_id', true) = '00000000-0000-0000-0000-000000000000'
    )
    WITH CHECK (
        org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
        OR current_setting('app.org_id', true) = '00000000-0000-0000-0000-000000000000'
    );

ALTER POLICY tenant_fence ON core.users
    USING (
        (
            org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
            OR current_setting('app.org_id', true) = '00000000-0000-0000-0000-000000000000'
        )
        AND (
            current_setting('app.identity_id', true) IS NULL
            OR current_setting('app.identity_id', true) = ''
            OR identity_id = NULLIF(current_setting('app.identity_id', true), '')::uuid
        )
    )
    WITH CHECK (
        (
            org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
            OR current_setting('app.org_id', true) = '00000000-0000-0000-0000-000000000000'
        )
        AND (
            current_setting('app.identity_id', true) IS NULL
            OR current_setting('app.identity_id', true) = ''
            OR identity_id = NULLIF(current_setting('app.identity_id', true), '')::uuid
        )
    );

ALTER POLICY tenant_fence ON core.memberships
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

ALTER POLICY tenant_fence ON core.sessions
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

ALTER POLICY tenant_fence ON core.api_keys
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

ALTER POLICY tenant_fence ON core.socket_tickets
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

ALTER POLICY tenant_fence ON core.tasks
    USING (
        org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
        OR current_setting('app.org_id', true) = '00000000-0000-0000-0000-000000000000'
    )
    WITH CHECK (
        org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
        OR current_setting('app.org_id', true) = '00000000-0000-0000-0000-000000000000'
    );

ALTER POLICY tenant_fence ON core.idempotency_records
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

ALTER POLICY tenant_fence ON core.outbox_rows
    USING (
        org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
        OR current_setting('app.org_id', true) = '00000000-0000-0000-0000-000000000000'
    )
    WITH CHECK (
        org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
        OR current_setting('app.org_id', true) = '00000000-0000-0000-0000-000000000000'
    );
