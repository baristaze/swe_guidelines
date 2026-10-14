-- A queued wake names its reason by the new name: the `WAKE_PARKED` items
-- whose reason is `resource` carry `leasable_resource`, under a counted lift
-- of FORCE in the migration's one transaction.

ALTER TABLE queue.work_items NO FORCE ROW LEVEL SECURITY;
DO $$
DECLARE
    meant bigint;
    touched bigint;
BEGIN
    SELECT count(*) INTO meant FROM queue.work_items WHERE kind = 'WAKE_PARKED' AND payload ->> 'reason' = 'resource';
    UPDATE queue.work_items SET payload = jsonb_set(payload, '{reason}', '"leasable_resource"') WHERE kind = 'WAKE_PARKED' AND payload ->> 'reason' = 'resource';
    GET DIAGNOSTICS touched = ROW_COUNT;
    IF touched <> meant THEN
        RAISE EXCEPTION 'queue.work_items: touched % rows of %', touched, meant;
    END IF;
END
$$;
ALTER TABLE queue.work_items FORCE ROW LEVEL SECURITY;
