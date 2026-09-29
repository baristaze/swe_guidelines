-- The claim takes the ready item that became ready first: ORDER BY
-- (available_at, id). This index walks exactly that order inside a lane and a
-- status, so a claim reads the first unlocked row and stops, however long the
-- ready backlog is. It carries the three columns of the index it replaces, so
-- every statement that index served is served here too. Plain CREATE INDEX:
-- the runner applies a role's chain in one transaction, where CONCURRENTLY is
-- refused.

CREATE INDEX ix_work_items_lane_status_available_at_id ON queue.work_items (lane, status, available_at, id);
DROP INDEX queue.ix_work_items_lane_status_available_at;
