-- The release before writes the position beside every rank itself, and
-- every row holds its rank's float, so the trigger goes with nothing lost.
CREATE INDEX ix_tasks_org_id_status_position ON core.tasks (org_id, status, position);
DROP TRIGGER tasks_position_from_rank ON core.tasks;
DROP FUNCTION core.tasks_position_from_rank();
