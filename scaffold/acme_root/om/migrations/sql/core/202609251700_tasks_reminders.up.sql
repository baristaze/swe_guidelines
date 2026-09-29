-- A task can carry a due time, and remembers when its reminder went out.
-- Expand only: both columns are nullable, so a build that predates them
-- keeps inserting and updating during the rollout.

ALTER TABLE core.tasks ADD COLUMN remind_at timestamptz;
ALTER TABLE core.tasks ADD COLUMN reminded_at timestamptz;
