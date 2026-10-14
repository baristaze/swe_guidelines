-- Gives the queued wakes their earlier reason back, under the same counted
-- lift of FORCE.

ALTER TABLE queue.work_items NO FORCE ROW LEVEL SECURITY;
DO $$
DECLARE
    meant bigint;
    touched bigint;
BEGIN
    SELECT count(*) INTO meant FROM queue.work_items WHERE kind = 'WAKE_PARKED' AND payload ->> 'reason' = 'leasable_resource';
    UPDATE queue.work_items SET payload = jsonb_set(payload, '{reason}', '"resource"') WHERE kind = 'WAKE_PARKED' AND payload ->> 'reason' = 'leasable_resource';
    GET DIAGNOSTICS touched = ROW_COUNT;
    IF touched <> meant THEN
        RAISE EXCEPTION 'queue.work_items: touched % rows of %', touched, meant;
    END IF;
END
$$;
ALTER TABLE queue.work_items FORCE ROW LEVEL SECURITY;
