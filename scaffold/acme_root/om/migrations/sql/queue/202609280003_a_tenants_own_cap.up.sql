-- A tenant's own cap on a lane: one row per tenant and lane, which the
-- claim joins to its count of the lane's claimed items, in place of the
-- lane's cap for that tenant. The serving logins' grants arrive by the
-- role's default privilege.

CREATE TABLE queue.tenant_caps (
    id uuid NOT NULL,
    org_id uuid NOT NULL,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    created_by uuid NOT NULL,
    updated_by uuid NOT NULL,
    lane text NOT NULL,
    cap integer NOT NULL,
    CONSTRAINT pk_tenant_caps PRIMARY KEY (id)
);
CREATE UNIQUE INDEX uq_tenant_caps_org_id_lane ON queue.tenant_caps (org_id, lane);

-- The second fence, as on every tenant table (ADR 0016): the transaction's
-- own tenant, or the system scope to the system login alone. An operator's
-- write reaches the one tenant it names; the claim reads the caps of the
-- lane it claims from under the system scope.

ALTER TABLE queue.tenant_caps ENABLE ROW LEVEL SECURITY;
ALTER TABLE queue.tenant_caps FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_fence ON queue.tenant_caps
    USING (
        org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
        OR (
            current_setting('app.org_id', true) = '00000000-0000-0000-0000-000000000000'
            AND current_user = 'acme_system'
        )
    )
    WITH CHECK (
        org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
        OR (
            current_setting('app.org_id', true) = '00000000-0000-0000-0000-000000000000'
            AND current_user = 'acme_system'
        )
    );
