-- The billing swimlane. An org's billing account mirrors its subscription at
-- the payment processor, one row per org; a delivery mark is the processor's
-- event already applied, written in the same commit as the account, so the
-- next copy of the event changes nothing. Both belong to the tenant and
-- carry the tenant fence every tenant table does.

CREATE TABLE core.billing_accounts (
    id uuid NOT NULL,
    org_id uuid NOT NULL,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    created_by uuid NOT NULL,
    updated_by uuid NOT NULL,
    customer_id text NULL,
    subscription_id text NULL,
    price_lookup_key text NULL,
    status text NULL,
    current_period_end timestamptz NULL,
    cancel_at_period_end boolean NOT NULL,
    quantity integer NOT NULL,
    comped_plan text NULL,
    synced_at timestamptz NULL,
    CONSTRAINT pk_billing_accounts PRIMARY KEY (id)
);
CREATE UNIQUE INDEX uq_billing_accounts_org_id ON core.billing_accounts (org_id);

CREATE TABLE core.billing_deliveries (
    id uuid NOT NULL,
    org_id uuid NOT NULL,
    created_at timestamptz NOT NULL,
    event_id text NOT NULL,
    event_type text NOT NULL,
    CONSTRAINT pk_billing_deliveries PRIMARY KEY (id)
);
CREATE INDEX ix_billing_deliveries_org_id_created_at ON core.billing_deliveries (org_id, created_at);

ALTER TABLE core.billing_accounts ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.billing_accounts FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_fence ON core.billing_accounts FOR ALL
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

ALTER TABLE core.billing_deliveries ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.billing_deliveries FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_fence ON core.billing_deliveries FOR ALL
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
