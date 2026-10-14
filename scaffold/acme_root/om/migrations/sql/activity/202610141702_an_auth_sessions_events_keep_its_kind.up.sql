-- An auth session's change events read under its kind: the events of kind
-- `tenancy.session.*` become `tenancy.auth_session.*` in place. FORCE binds
-- the migration login that owns the table, so the move lifts it for its own
-- statement, counts the rows it means to touch, fails on any other count,
-- and puts it back, in the migration's one transaction.

ALTER TABLE activity.events NO FORCE ROW LEVEL SECURITY;
DO $$
DECLARE
    meant bigint;
    touched bigint;
BEGIN
    SELECT count(*) INTO meant FROM activity.events WHERE starts_with(kind, 'tenancy.session.');
    UPDATE activity.events SET kind = 'tenancy.auth_session.' || substr(kind, 17) WHERE starts_with(kind, 'tenancy.session.');
    GET DIAGNOSTICS touched = ROW_COUNT;
    IF touched <> meant THEN
        RAISE EXCEPTION 'activity.events: touched % rows of %', touched, meant;
    END IF;
END
$$;
ALTER TABLE activity.events FORCE ROW LEVEL SECURITY;
