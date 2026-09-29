-- The invitations go with their table, and the links to the provider with
-- their columns: a person linked meanwhile is found by the email again. An
-- identity made meanwhile has no password; it gets a hash no password
-- matches, so the column can hold NOT NULL again and the release before
-- this one refuses its sign-in as any wrong password.

DROP POLICY tenant_fence ON core.invitations;
DROP TABLE core.invitations;

DROP INDEX core.uq_orgs_provider_org_id;
ALTER TABLE core.orgs DROP COLUMN provider_org_id;

DROP INDEX core.uq_identities_issuer_subject;
ALTER TABLE core.identities DROP COLUMN subject;
ALTER TABLE core.identities DROP COLUMN issuer;

UPDATE core.identities SET password_hash = 'none' WHERE password_hash IS NULL;
ALTER TABLE core.identities ALTER COLUMN password_hash SET NOT NULL;
