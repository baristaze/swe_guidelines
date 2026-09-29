DROP INDEX core.uq_memberships_org_id_user_id;
CREATE UNIQUE INDEX uq_memberships_org_id_user_id ON core.memberships (org_id, user_id);
DROP INDEX core.uq_orgs_slug;
CREATE UNIQUE INDEX uq_orgs_slug ON core.orgs (slug);
