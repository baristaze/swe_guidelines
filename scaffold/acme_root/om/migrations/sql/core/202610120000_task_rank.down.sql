-- The release before orders by the position, which this release keeps
-- current beside every rank it writes, so the rank goes with nothing lost.
DROP INDEX core.ix_tasks_org_id_rank_long;
DROP INDEX core.ix_tasks_org_id_status_rank;
DROP TRIGGER tasks_rank_from_position ON core.tasks;
DROP FUNCTION core.tasks_rank_from_position();
ALTER TABLE core.tasks DROP COLUMN rank;
