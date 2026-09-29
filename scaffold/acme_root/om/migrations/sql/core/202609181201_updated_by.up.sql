-- Trackable records who last changed a row, not only who created it. The
-- backfill names the creator, the only principal the rows remember; the
-- object model sets the value from the context on every update.

ALTER TABLE core.orgs ADD COLUMN updated_by uuid NULL;
UPDATE core.orgs SET updated_by = created_by;
ALTER TABLE core.orgs ALTER COLUMN updated_by SET NOT NULL;
ALTER TABLE core.identities ADD COLUMN updated_by uuid NULL;
UPDATE core.identities SET updated_by = created_by;
ALTER TABLE core.identities ALTER COLUMN updated_by SET NOT NULL;
ALTER TABLE core.users ADD COLUMN updated_by uuid NULL;
UPDATE core.users SET updated_by = created_by;
ALTER TABLE core.users ALTER COLUMN updated_by SET NOT NULL;
ALTER TABLE core.memberships ADD COLUMN updated_by uuid NULL;
UPDATE core.memberships SET updated_by = created_by;
ALTER TABLE core.memberships ALTER COLUMN updated_by SET NOT NULL;
ALTER TABLE core.sessions ADD COLUMN updated_by uuid NULL;
UPDATE core.sessions SET updated_by = created_by;
ALTER TABLE core.sessions ALTER COLUMN updated_by SET NOT NULL;
ALTER TABLE core.api_keys ADD COLUMN updated_by uuid NULL;
UPDATE core.api_keys SET updated_by = created_by;
ALTER TABLE core.api_keys ALTER COLUMN updated_by SET NOT NULL;
ALTER TABLE core.tasks ADD COLUMN updated_by uuid NULL;
UPDATE core.tasks SET updated_by = created_by;
ALTER TABLE core.tasks ALTER COLUMN updated_by SET NOT NULL;
