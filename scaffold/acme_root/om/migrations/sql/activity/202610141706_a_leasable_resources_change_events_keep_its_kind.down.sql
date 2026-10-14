-- Moves a leasable resource's change events back to their earlier kind,
-- under the same counted lift of FORCE.

ALTER TABLE activity.change_events NO FORCE ROW LEVEL SECURITY;
DO $$
DECLARE
    meant bigint;
    touched bigint;
BEGIN
    SELECT count(*) INTO meant FROM activity.change_events WHERE starts_with(kind, 'leases.leasable_resource.');
    UPDATE activity.change_events SET kind = 'leases.resource.' || substr(kind, 26) WHERE starts_with(kind, 'leases.leasable_resource.');
    GET DIAGNOSTICS touched = ROW_COUNT;
    IF touched <> meant THEN
        RAISE EXCEPTION 'activity.change_events: touched % rows of %', touched, meant;
    END IF;
END
$$;
ALTER TABLE activity.change_events FORCE ROW LEVEL SECURITY;
