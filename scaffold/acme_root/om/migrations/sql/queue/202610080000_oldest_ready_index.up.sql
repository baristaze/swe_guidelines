-- The sweep reads the age of the item ready longest on any lane, once a
-- pass, for the backlog alarm. The claim's index leads with the lane, so
-- across lanes it gives no order by readiness, and the read would visit every
-- queued item, the ones parked until later included. This partial index holds
-- the queued items alone, in the order they become ready, so the read is its
-- first entry. Plain CREATE INDEX: the runner applies a role's chain in one
-- transaction, where CONCURRENTLY is refused.

CREATE INDEX ix_work_items_available_at_queued ON queue.work_items (available_at)
    WHERE status = 'queued';
