-- Puts back the narrowing the first chain wrote: `identity_id` against
-- `app.user_id`.

ALTER POLICY tenant_fence ON core.users
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
