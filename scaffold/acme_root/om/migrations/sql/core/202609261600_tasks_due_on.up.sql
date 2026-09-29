-- A task is due on a date, never at a time, and a person carries the time
-- zone a due date's reminder is timed in. Expand only: both columns are
-- nullable, so the release before keeps inserting and updating during the
-- rollout. `tasks.remind_at` stays for that release, which reads it; the
-- release after this one drops it.

ALTER TABLE core.tasks ADD COLUMN due_on date;
ALTER TABLE core.identities ADD COLUMN time_zone text;
