-- Drops the index of the queued items by readiness.

DROP INDEX queue.ix_work_items_available_at_queued;
