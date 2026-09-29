-- The release before reads only the due time, so every task whose due time
-- does not fall on its due date takes nine in the morning of that date, UTC,
-- and a task with no due date has no due time. A task written by this
-- release holds that already; this catches any other. Counted and fenced as
-- the upgrade is.

ALTER TABLE core.tasks NO FORCE ROW LEVEL SECURITY;

CREATE TEMP TABLE remind_at_restore ON COMMIT DROP AS
SELECT count(*) AS expected FROM core.tasks
WHERE (remind_at AT TIME ZONE 'UTC')::date IS DISTINCT FROM due_on;

WITH touched AS (
    UPDATE core.tasks
    SET remind_at = CASE WHEN due_on IS NULL THEN NULL
        ELSE (due_on + time '09:00') AT TIME ZONE 'UTC' END
    WHERE (remind_at AT TIME ZONE 'UTC')::date IS DISTINCT FROM due_on
    RETURNING 1
)
SELECT CASE WHEN t.count <> b.expected
    THEN ('due time restore touched ' || t.count || ' rows of ' || b.expected)::int END
FROM (SELECT count(*) AS count FROM touched) t, remind_at_restore b;

ALTER TABLE core.tasks FORCE ROW LEVEL SECURITY;
