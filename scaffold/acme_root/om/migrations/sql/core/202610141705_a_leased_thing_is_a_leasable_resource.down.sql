-- Gives the leased things their earlier name back, every row kept, and
-- moves their stored values back under the same counted lift of FORCE; the
-- fence, the grants, and the indexes follow the table.

ALTER TABLE core.outbox_rows NO FORCE ROW LEVEL SECURITY;
DO $$
DECLARE
    meant bigint;
    touched bigint;
BEGIN
    SELECT count(*) INTO meant FROM core.outbox_rows WHERE starts_with(kind, 'leases.leasable_resource.');
    UPDATE core.outbox_rows SET kind = 'leases.resource.' || substr(kind, 26) WHERE starts_with(kind, 'leases.leasable_resource.');
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
    SELECT count(*) INTO meant FROM core.outbox_rows WHERE kind = 'work.WAKE_PARKED' AND payload ->> 'reason' = 'leasable_resource';
    UPDATE core.outbox_rows SET payload = jsonb_set(payload, '{reason}', '"resource"') WHERE kind = 'work.WAKE_PARKED' AND payload ->> 'reason' = 'leasable_resource';
    GET DIAGNOSTICS touched = ROW_COUNT;
    IF touched <> meant THEN
        RAISE EXCEPTION 'core.outbox_rows: touched % rows of %', touched, meant;
    END IF;
END
$$;
ALTER TABLE core.outbox_rows FORCE ROW LEVEL SECURITY;

ALTER TABLE core.orchestrations NO FORCE ROW LEVEL SECURITY;
DO $$
DECLARE
    meant bigint;
    touched bigint;
BEGIN
    SELECT count(*) INTO meant FROM core.orchestrations WHERE park_reason = 'leasable_resource';
    UPDATE core.orchestrations SET park_reason = 'resource' WHERE park_reason = 'leasable_resource';
    GET DIAGNOSTICS touched = ROW_COUNT;
    IF touched <> meant THEN
        RAISE EXCEPTION 'core.orchestrations: touched % rows of %', touched, meant;
    END IF;
END
$$;
ALTER TABLE core.orchestrations FORCE ROW LEVEL SECURITY;

ALTER INDEX core.ix_lease_requests_org_id_leasable_resource_id_waiting
    RENAME TO ix_lease_requests_org_id_resource_id_waiting;
ALTER TABLE core.lease_requests RENAME COLUMN leasable_resource_id TO resource_id;
ALTER INDEX core.ix_leases_org_id_leasable_resource_id_created_at_id
    RENAME TO ix_leases_org_id_resource_id_created_at_id;
ALTER INDEX core.uq_leases_org_id_leasable_resource_id_active
    RENAME TO uq_leases_org_id_resource_id_active;
ALTER TABLE core.leases RENAME COLUMN leasable_resource_id TO resource_id;
ALTER INDEX core.ix_leasable_resources_retired_at RENAME TO ix_resources_retired_at;
ALTER INDEX core.ix_leasable_resources_org_id_kind_free RENAME TO ix_resources_org_id_kind_free;
ALTER INDEX core.ix_leasable_resources_org_id_kind_id RENAME TO ix_resources_org_id_kind_id;
ALTER INDEX core.uq_leasable_resources_org_id_kind_ref_id RENAME TO uq_resources_org_id_kind_ref_id;
ALTER TABLE core.leasable_resources RENAME CONSTRAINT pk_leasable_resources TO pk_resources;
ALTER TABLE core.leasable_resources RENAME TO resources;
