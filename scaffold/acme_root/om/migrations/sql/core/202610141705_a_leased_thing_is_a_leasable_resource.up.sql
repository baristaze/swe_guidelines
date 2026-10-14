-- A leased thing takes the name leasable resource. A rename keeps every
-- row, the grants, and the row-level security, which follow the table; the
-- constraint, the indexes, and the column that names it on the leases and
-- the requests take the new name. The fence is spelled again on the new
-- name, so the chain names every table it holds.
--
-- The stored values that name it move in place: the park reason `resource`
-- on the records and on the wake rows the outbox holds, and the outbox kinds
-- `leases.resource.*`. FORCE binds the migration login that owns the tables,
-- so each move lifts it for its own statement, counts the rows it means to
-- touch, fails on any other count, and puts it back, all in the migration's
-- one transaction.

ALTER TABLE core.resources RENAME TO leasable_resources;
ALTER TABLE core.leasable_resources RENAME CONSTRAINT pk_resources TO pk_leasable_resources;
ALTER INDEX core.uq_resources_org_id_kind_ref_id RENAME TO uq_leasable_resources_org_id_kind_ref_id;
ALTER INDEX core.ix_resources_org_id_kind_id RENAME TO ix_leasable_resources_org_id_kind_id;
ALTER INDEX core.ix_resources_org_id_kind_free RENAME TO ix_leasable_resources_org_id_kind_free;
ALTER INDEX core.ix_resources_retired_at RENAME TO ix_leasable_resources_retired_at;
ALTER TABLE core.leases RENAME COLUMN resource_id TO leasable_resource_id;
ALTER INDEX core.uq_leases_org_id_resource_id_active
    RENAME TO uq_leases_org_id_leasable_resource_id_active;
ALTER INDEX core.ix_leases_org_id_resource_id_created_at_id
    RENAME TO ix_leases_org_id_leasable_resource_id_created_at_id;
ALTER TABLE core.lease_requests RENAME COLUMN resource_id TO leasable_resource_id;
ALTER INDEX core.ix_lease_requests_org_id_resource_id_waiting
    RENAME TO ix_lease_requests_org_id_leasable_resource_id_waiting;

ALTER TABLE core.leasable_resources ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.leasable_resources FORCE ROW LEVEL SECURITY;
DROP POLICY tenant_fence ON core.leasable_resources;
CREATE POLICY tenant_fence ON core.leasable_resources
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

ALTER TABLE core.orchestrations NO FORCE ROW LEVEL SECURITY;
DO $$
DECLARE
    meant bigint;
    touched bigint;
BEGIN
    SELECT count(*) INTO meant FROM core.orchestrations WHERE park_reason = 'resource';
    UPDATE core.orchestrations SET park_reason = 'leasable_resource' WHERE park_reason = 'resource';
    GET DIAGNOSTICS touched = ROW_COUNT;
    IF touched <> meant THEN
        RAISE EXCEPTION 'core.orchestrations: touched % rows of %', touched, meant;
    END IF;
END
$$;
ALTER TABLE core.orchestrations FORCE ROW LEVEL SECURITY;

ALTER TABLE core.outbox_rows NO FORCE ROW LEVEL SECURITY;
DO $$
DECLARE
    meant bigint;
    touched bigint;
BEGIN
    SELECT count(*) INTO meant FROM core.outbox_rows WHERE kind = 'work.WAKE_PARKED' AND payload ->> 'reason' = 'resource';
    UPDATE core.outbox_rows SET payload = jsonb_set(payload, '{reason}', '"leasable_resource"') WHERE kind = 'work.WAKE_PARKED' AND payload ->> 'reason' = 'resource';
    GET DIAGNOSTICS touched = ROW_COUNT;
    IF touched <> meant THEN
        RAISE EXCEPTION 'core.outbox_rows: touched % rows of %', touched, meant;
    END IF;
END
$$;
ALTER TABLE core.outbox_rows FORCE ROW LEVEL SECURITY;

ALTER TABLE core.outbox_rows NO FORCE ROW LEVEL SECURITY;
DO $$
DECLARE
    meant bigint;
    touched bigint;
BEGIN
    SELECT count(*) INTO meant FROM core.outbox_rows WHERE starts_with(kind, 'leases.resource.');
    UPDATE core.outbox_rows SET kind = 'leases.leasable_resource.' || substr(kind, 17) WHERE starts_with(kind, 'leases.resource.');
    GET DIAGNOSTICS touched = ROW_COUNT;
    IF touched <> meant THEN
        RAISE EXCEPTION 'core.outbox_rows: touched % rows of %', touched, meant;
    END IF;
END
$$;
ALTER TABLE core.outbox_rows FORCE ROW LEVEL SECURITY;
