ALTER TABLE queue.work_items DROP COLUMN updated_by;
ALTER INDEX queue.ix_work_items_lane_status_available_at RENAME TO ix_work_items_queue_status_available_at;
ALTER TABLE queue.work_items RENAME COLUMN lane TO queue;
