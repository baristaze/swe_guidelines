-- Every task with a due time takes its date as the due date: the date of the
-- time in UTC, since no person's time zone is known yet. A task whose due
-- date is set already is left as it is, so a rerun touches nothing.
--
-- The rows it means to touch are counted first and compared with the rows
-- the update reports; a difference fails the cast below, and the whole
-- transaction rolls back, the fence included. A migration names no tenant
-- and FORCE binds the login that owns the table, so the fence is lifted for
-- these statements and put back after.

ALTER TABLE core.tasks NO FORCE ROW LEVEL SECURITY;

CREATE TEMP TABLE due_on_backfill ON COMMIT DROP AS
SELECT count(*) AS expected FROM core.tasks WHERE remind_at IS NOT NULL AND due_on IS NULL;

WITH touched AS (
    UPDATE core.tasks SET due_on = (remind_at AT TIME ZONE 'UTC')::date
    WHERE remind_at IS NOT NULL AND due_on IS NULL
    RETURNING 1
)
SELECT CASE WHEN t.count <> b.expected
    THEN ('due date backfill touched ' || t.count || ' rows of ' || b.expected)::int END
FROM (SELECT count(*) AS count FROM touched) t, due_on_backfill b;

ALTER TABLE core.tasks FORCE ROW LEVEL SECURITY;
