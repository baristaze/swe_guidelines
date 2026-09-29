-- An org says what it is for (`kind`, personal or team), and a personal org
-- names its person. Expand only: the kind defaults to team, which is every
-- org the release before this one makes, and the person is nullable, so that
-- release keeps working on it while a rollout runs both. The next migration
-- gives every existing person their personal org.

ALTER TABLE core.orgs ADD COLUMN kind text NOT NULL DEFAULT 'team';
ALTER TABLE core.orgs ADD COLUMN personal_identity_id uuid NULL;
ALTER TABLE core.orgs ADD CONSTRAINT ck_orgs_personal_identity CHECK ((kind = 'personal') = (personal_identity_id IS NOT NULL));
-- One personal org per person, among the living.
CREATE UNIQUE INDEX uq_orgs_personal_identity_id ON core.orgs (personal_identity_id) WHERE kind = 'personal' AND deleted_at IS NULL;
