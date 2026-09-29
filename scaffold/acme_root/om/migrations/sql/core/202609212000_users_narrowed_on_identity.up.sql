-- A user's person is the identity behind it, so `core.users` is narrowed on
-- `identity_id` against the identity setting. The first chain compared it
-- with `app.user_id`, which the funnel fills with a user id, so the first
-- read that narrowed a user by person would have come back empty. Every
-- other both-scoped table names the user and stays on `app.user_id`.
--
-- ALTER, not DROP and CREATE: the table keeps its one policy under the same
-- name, and only the narrowing changes.

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
