-- A leasable resource's change events read under its kind: the events of kind
-- `leases.resource.*` become `leases.leasable_resource.*` in place, under a
-- counted lift of FORCE in the migration's one transaction.

ALTER TABLE activity.change_events NO FORCE ROW LEVEL SECURITY;
DO $$
DECLARE
    meant bigint;
    touched bigint;
BEGIN
    SELECT count(*) INTO meant FROM activity.change_events WHERE starts_with(kind, 'leases.resource.');
    UPDATE activity.change_events SET kind = 'leases.leasable_resource.' || substr(kind, 17) WHERE starts_with(kind, 'leases.resource.');
    GET DIAGNOSTICS touched = ROW_COUNT;
    IF touched <> meant THEN
        RAISE EXCEPTION 'activity.change_events: touched % rows of %', touched, meant;
    END IF;
END
$$;
ALTER TABLE activity.change_events FORCE ROW LEVEL SECURITY;
