-- The position leaves the mapping: this release reads the rank alone and
-- names the position in no statement. The release before still names it in
-- every insert and reads it as a number, during the rollout and again after
-- a fast rollback, so the database keeps it the rank's float (ADR 0050).
-- The column stays NOT NULL: this trigger fills it before the check.
--
-- A row this release inserts names no position; one it moves changes the
-- rank and leaves the position as it was. The trigger gives either the float
-- of its rank. That is what the release before reads, and it keeps the
-- trigger of 202610120000 (which fills the rank from a position that changed
-- alone) from ever meeting a stale position. A row the release before
-- writes names both, the position as the rank's float, and this trigger
-- leaves it alone. Both triggers, their functions, and the column go in the
-- release after this one.
CREATE FUNCTION core.tasks_position_from_rank() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.position IS NULL
        OR (TG_OP = 'UPDATE' AND NEW.rank IS DISTINCT FROM OLD.rank AND NEW.position = OLD.position)
    THEN
        NEW.position := NEW.rank::double precision;
    END IF;
    RETURN NEW;
END
$$;

CREATE TRIGGER tasks_position_from_rank BEFORE INSERT OR UPDATE ON core.tasks
FOR EACH ROW EXECUTE FUNCTION core.tasks_position_from_rank();

-- No statement of this release or of the release before reads the
-- position's index; every write kept it for nothing.
DROP INDEX core.ix_tasks_org_id_status_position;
