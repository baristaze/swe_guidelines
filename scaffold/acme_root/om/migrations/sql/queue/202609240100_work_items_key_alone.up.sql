-- The idempotency key is unique within its tenant and nowhere else. The index
-- on the key alone stayed beside the tenant's while a release that predates
-- the tenant's index could still be serving, and it read a taken key as a
-- retry. Nothing serving reads it now, so it goes; two tenants may hold one
-- key from here on.
DROP INDEX queue.uq_work_items_idempotency_key;
