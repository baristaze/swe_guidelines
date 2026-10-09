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

-- The queue's fence, as on the work items: one policy per login (ADR 0044).
-- The runtime login reaches the tenant the transaction names, which is how
-- an operator's write reaches the one tenant it names; the system login
-- reaches every tenant under the system scope, which is how the claim
-- reads the caps of the lane it claims from.

ALTER TABLE queue.tenant_caps ENABLE ROW LEVEL SECURITY;
ALTER TABLE queue.tenant_caps FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_fence ON queue.tenant_caps FOR ALL TO acme_runtime
    USING (org_id = NULLIF(current_setting('app.org_id', true), '')::uuid)
    WITH CHECK (org_id = NULLIF(current_setting('app.org_id', true), '')::uuid);
CREATE POLICY system_fence ON queue.tenant_caps FOR ALL TO acme_system
    USING (current_setting('app.org_id', true) = '00000000-0000-0000-0000-000000000000')
    WITH CHECK (current_setting('app.org_id', true) = '00000000-0000-0000-0000-000000000000');
