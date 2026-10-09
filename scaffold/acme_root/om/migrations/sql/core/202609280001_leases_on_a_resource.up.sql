-- Leases on a resource: the resources, each with the lease store's anchor
-- (the highest token granted, the lease that holds it, and until when), the
-- leases granted on them, and the requests in line in front of them. Each
-- table carries the tenant's fence, and the serving logins' grants arrive by
-- the role's default privilege.

-- One row per leasable thing, unique by its org, its kind, and the row it
-- stands for. The anchor is the row a grant locks.
CREATE TABLE core.resources (
    id uuid NOT NULL,
    org_id uuid NOT NULL,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    created_by uuid NOT NULL,
    updated_by uuid NOT NULL,
    kind text NOT NULL,
    ref_id uuid NOT NULL,
    labels jsonb NOT NULL,
    max_term_seconds integer NOT NULL,
    available boolean NOT NULL,
    retired_at timestamptz NULL,
    token bigint NOT NULL,
    lease_id uuid NULL,
    held_until timestamptz NULL,
    mean_hold_seconds double precision NULL,
    CONSTRAINT pk_resources PRIMARY KEY (id)
);
CREATE UNIQUE INDEX uq_resources_org_id_kind_ref_id ON core.resources (org_id, kind, ref_id);
CREATE INDEX ix_resources_org_id_kind_id ON core.resources (org_id, kind, id);
CREATE INDEX ix_resources_org_id_kind_free ON core.resources (org_id, kind)
    WHERE lease_id IS NULL AND available AND retired_at IS NULL;
CREATE INDEX ix_resources_retired_at ON core.resources (retired_at) WHERE retired_at IS NOT NULL;

-- One grant of one resource. The partial unique index is the second fence:
-- one active lease per resource, whatever passed the anchor's lock; and one
-- lease per request.
CREATE TABLE core.leases (
    id uuid NOT NULL,
    org_id uuid NOT NULL,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    created_by uuid NOT NULL,
    updated_by uuid NOT NULL,
    resource_id uuid NOT NULL,
    request_id uuid NOT NULL,
    holder_id uuid NOT NULL,
    token bigint NOT NULL,
    term_seconds integer NOT NULL,
    expires_at timestamptz NOT NULL,
    status text NOT NULL,
    ended_at timestamptz NULL,
    CONSTRAINT pk_leases PRIMARY KEY (id)
);
CREATE UNIQUE INDEX uq_leases_org_id_resource_id_active ON core.leases (org_id, resource_id)
    WHERE status = 'active';
CREATE UNIQUE INDEX uq_leases_org_id_request_id ON core.leases (org_id, request_id);
CREATE INDEX ix_leases_org_id_expires_at_active ON core.leases (org_id, expires_at)
    WHERE status = 'active';
CREATE INDEX ix_leases_expires_at_active ON core.leases (expires_at) WHERE status = 'active';
CREATE INDEX ix_leases_ended_at ON core.leases (ended_at) WHERE ended_at IS NOT NULL;

-- The line: a request names one resource or a selector, and holds one place
-- in its org's rank order. An ask asked again by its key meets the key.
CREATE TABLE core.lease_requests (
    id uuid NOT NULL,
    org_id uuid NOT NULL,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    created_by uuid NOT NULL,
    updated_by uuid NOT NULL,
    idempotency_key uuid NOT NULL,
    kind text NOT NULL,
    resource_id uuid NULL,
    labels jsonb NULL,
    payload jsonb NOT NULL,
    waiter_kind text NULL,
    waiter_id uuid NULL,
    term_seconds integer NOT NULL,
    wait_seconds integer NOT NULL,
    wait_until timestamptz NOT NULL,
    rank double precision NOT NULL,
    status text NOT NULL,
    end_reason text NULL,
    lease_id uuid NULL,
    CONSTRAINT pk_lease_requests PRIMARY KEY (id)
);
CREATE UNIQUE INDEX uq_lease_requests_org_id_idempotency_key
    ON core.lease_requests (org_id, idempotency_key);
CREATE INDEX ix_lease_requests_org_id_kind_rank_id_waiting
    ON core.lease_requests (org_id, kind, rank, id) WHERE status = 'waiting';
CREATE INDEX ix_lease_requests_org_id_wait_until_waiting
    ON core.lease_requests (org_id, wait_until) WHERE status = 'waiting';
CREATE INDEX ix_lease_requests_wait_until_waiting
    ON core.lease_requests (wait_until) WHERE status = 'waiting';
CREATE INDEX ix_lease_requests_org_id_waiter_kind_waiter_id_waiting
    ON core.lease_requests (org_id, waiter_kind, waiter_id) WHERE status = 'waiting';
CREATE INDEX ix_lease_requests_org_id_resource_id_waiting
    ON core.lease_requests (org_id, resource_id) WHERE status = 'waiting';
CREATE INDEX ix_lease_requests_updated_at_settled
    ON core.lease_requests (updated_at) WHERE status <> 'waiting';

-- The second fence, as on every tenant table of the role.
ALTER TABLE core.resources ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.resources FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_fence ON core.resources
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

ALTER TABLE core.leases ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.leases FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_fence ON core.leases
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

ALTER TABLE core.lease_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.lease_requests FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_fence ON core.lease_requests
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
