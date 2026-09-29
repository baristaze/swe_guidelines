-- Puts back the system-scope clause without the login: the empty UUID alone.

ALTER POLICY tenant_fence ON activity.events
    USING (
        org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
        OR current_setting('app.org_id', true) = '00000000-0000-0000-0000-000000000000'
    )
    WITH CHECK (
        org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
        OR current_setting('app.org_id', true) = '00000000-0000-0000-0000-000000000000'
    );

ALTER POLICY tenant_fence ON activity.event_cursors
    USING (
        org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
        OR current_setting('app.org_id', true) = '00000000-0000-0000-0000-000000000000'
    )
    WITH CHECK (
        org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
        OR current_setting('app.org_id', true) = '00000000-0000-0000-0000-000000000000'
    );
