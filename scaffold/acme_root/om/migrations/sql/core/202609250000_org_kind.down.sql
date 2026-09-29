-- The personal orgs made meanwhile stay, as orgs like any other: a downgrade
-- drops what tells them apart, never a tenant's rows.

DROP INDEX core.uq_orgs_personal_identity_id;
ALTER TABLE core.orgs DROP CONSTRAINT ck_orgs_personal_identity;
ALTER TABLE core.orgs DROP COLUMN personal_identity_id;
ALTER TABLE core.orgs DROP COLUMN kind;
