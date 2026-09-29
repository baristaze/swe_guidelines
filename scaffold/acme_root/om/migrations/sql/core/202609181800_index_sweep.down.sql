DROP INDEX core.uq_users_org_id_identity_id_live;
CREATE INDEX ix_users_org_id ON core.users (org_id);
CREATE INDEX ix_orgs_org_id ON core.orgs (org_id);
