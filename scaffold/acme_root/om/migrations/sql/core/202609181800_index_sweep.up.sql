-- Two indexes go and one arrives. org_id on orgs is the row's own id, so its
-- index duplicated the primary key. On users, (org_id, identity_id) now leads a
-- partial unique index that holds "one live user per identity in a tenant", the
-- rule add_member reads for; org_id leads it, so its single-column index goes.

DROP INDEX core.ix_orgs_org_id;
DROP INDEX core.ix_users_org_id;
CREATE UNIQUE INDEX uq_users_org_id_identity_id_live ON core.users (org_id, identity_id) WHERE deleted_at IS NULL;
