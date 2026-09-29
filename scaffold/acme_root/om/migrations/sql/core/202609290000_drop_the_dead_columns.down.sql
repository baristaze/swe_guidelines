-- The four columns come back empty, in the shape the release before this one
-- declares, so its schema check agrees. Three are nullable and hold NULL;
-- `failed_sign_ins` keeps its default of zero, which every row takes. A
-- downgrade restores the shape, not the data. The downgrade of 202609261601
-- gives each task with a due date a due time again, and the one of
-- 202609261200 gives each identity a hash no password matches.

ALTER TABLE core.identities ADD COLUMN password_hash text NULL;
ALTER TABLE core.identities ADD COLUMN failed_sign_ins integer NOT NULL DEFAULT 0;
ALTER TABLE core.identities ADD COLUMN last_failed_sign_in_at timestamptz NULL;
ALTER TABLE core.tasks ADD COLUMN remind_at timestamptz NULL;
