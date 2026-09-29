-- Every audit row names the request that produced it. The default backfills
-- the rows already appended and is dropped at once; the object model sets
-- the value from the context.

ALTER TABLE activity.events ADD COLUMN request_id uuid NOT NULL DEFAULT '00000000-0000-0000-0000-000000000000';
ALTER TABLE activity.events ALTER COLUMN request_id DROP DEFAULT;
