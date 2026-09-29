-- The routing field of a work item is its lane; "queue" names the interface,
-- the table, and the role, not the row's field. Trackable gains updated_by.
-- A one-step rename, not expand and contract: ADR 0006.

ALTER TABLE queue.work_items RENAME COLUMN queue TO lane;
ALTER INDEX queue.ix_work_items_queue_status_available_at RENAME TO ix_work_items_lane_status_available_at;
ALTER TABLE queue.work_items ADD COLUMN updated_by uuid NULL;
UPDATE queue.work_items SET updated_by = created_by;
ALTER TABLE queue.work_items ALTER COLUMN updated_by SET NOT NULL;
