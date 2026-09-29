ALTER TABLE core.orgs DROP COLUMN updated_by;
ALTER TABLE core.identities DROP COLUMN updated_by;
ALTER TABLE core.users DROP COLUMN updated_by;
ALTER TABLE core.memberships DROP COLUMN updated_by;
ALTER TABLE core.sessions DROP COLUMN updated_by;
ALTER TABLE core.api_keys DROP COLUMN updated_by;
ALTER TABLE core.tasks DROP COLUMN updated_by;
