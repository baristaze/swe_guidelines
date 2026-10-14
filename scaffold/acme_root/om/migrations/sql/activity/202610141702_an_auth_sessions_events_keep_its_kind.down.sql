-- Moves the auth session's change events back to their earlier kind, under
-- the same counted lift of FORCE.

ALTER TABLE activity.events NO FORCE ROW LEVEL SECURITY;
DO $$
DECLARE
    meant bigint;
    touched bigint;
BEGIN
    SELECT count(*) INTO meant FROM activity.events WHERE starts_with(kind, 'tenancy.auth_session.');
    UPDATE activity.events SET kind = 'tenancy.session.' || substr(kind, 22) WHERE starts_with(kind, 'tenancy.auth_session.');
    GET DIAGNOSTICS touched = ROW_COUNT;
    IF touched <> meant THEN
        RAISE EXCEPTION 'activity.events: touched % rows of %', touched, meant;
    END IF;
END
$$;
ALTER TABLE activity.events FORCE ROW LEVEL SECURITY;
