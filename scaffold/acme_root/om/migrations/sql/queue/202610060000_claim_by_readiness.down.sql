-- Puts the three-column index back.

CREATE INDEX ix_work_items_lane_status_available_at ON queue.work_items (lane, status, available_at);
DROP INDEX queue.ix_work_items_lane_status_available_at_id;
