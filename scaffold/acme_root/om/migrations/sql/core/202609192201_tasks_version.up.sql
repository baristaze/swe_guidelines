-- A task carries a version: every update, move, and soft delete increments
-- it and lands only when the stored row still has the version the caller
-- read, so a stale snapshot cannot overwrite a write it did not see. Expand
-- only: every row written before this is at version 1, and the default
-- keeps a build that predates the column inserting during the rollout.

ALTER TABLE core.tasks ADD COLUMN version integer NOT NULL DEFAULT 1;
