-- An open task's place in the list is a rank: an exact decimal, so there is
-- always one between two others and a move writes one row (ADR 0050). Expand
-- only. The position stays for the release before, which orders by it; this
-- release writes both, and the release after drops the position, the
-- trigger, and the function with it.

ALTER TABLE core.tasks ADD COLUMN rank numeric;

-- Every task takes its position as its rank, digit for digit: a float's text
-- is the shortest that reads back as the same float, so two positions that
-- differ give two ranks that differ the same way, and a cursor the release
-- before issued names a rank that exists. Counted first and compared, as
-- every backfill here is, under the fence lifted for these statements.
ALTER TABLE core.tasks NO FORCE ROW LEVEL SECURITY;

CREATE TEMP TABLE rank_backfill ON COMMIT DROP AS
SELECT count(*) AS expected FROM core.tasks WHERE rank IS NULL;

WITH touched AS (
    UPDATE core.tasks SET rank = position::text::numeric
    WHERE rank IS NULL
    RETURNING 1
)
SELECT CASE WHEN t.count <> b.expected
    THEN ('rank backfill touched ' || t.count || ' rows of ' || b.expected)::int END
FROM (SELECT count(*) AS count FROM touched) t, rank_backfill b;

ALTER TABLE core.tasks FORCE ROW LEVEL SECURITY;

-- The release before inserts a task without a rank and moves one by its
-- position alone. The trigger gives such a row the rank its position names,
-- so a row that release writes during the rollout, or after a rollback, has
-- a rank this release orders by. A write of this release sets the rank
-- itself and the position beside it, and the trigger leaves it alone.
CREATE FUNCTION core.tasks_rank_from_position() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.rank IS NULL
        OR (TG_OP = 'UPDATE' AND NEW.rank = OLD.rank AND NEW.position IS DISTINCT FROM OLD.position)
    THEN
        NEW.rank := NEW.position::text::numeric;
    END IF;
    RETURN NEW;
END
$$;

CREATE TRIGGER tasks_rank_from_position BEFORE INSERT OR UPDATE ON core.tasks
FOR EACH ROW EXECUTE FUNCTION core.tasks_rank_from_position();

-- The trigger runs before the check, so a row the release before inserts
-- meets NOT NULL with its rank already set.
ALTER TABLE core.tasks ALTER COLUMN rank SET NOT NULL;

-- The open list in rank order. The position's index stays for the release
-- before and goes with the column.
CREATE INDEX ix_tasks_org_id_status_rank ON core.tasks (org_id, status, rank);

-- The sweep's respace finds a tenant's rank that grew past 24 digits after
-- the point (tasks.rules.RANK_SCALE_BOUND). Only such ranks are in it. The
-- bound is a literal, which the storage writes as one too.
CREATE INDEX ix_tasks_org_id_rank_long ON core.tasks (org_id, rank) WHERE scale(rank) > 24 AND deleted_at IS NULL;
