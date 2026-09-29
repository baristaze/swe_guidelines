-- A unique key on a soft-deletable table is unique among the living. An org's
-- slug and a user's membership in a tenant become partial unique indexes, so a
-- deleted org frees its slug and an ended membership frees its (org, user),
-- and the same value can be created again. The api key hash stays full: it
-- digests a fresh random secret and is never created again.

DROP INDEX core.uq_orgs_slug;
CREATE UNIQUE INDEX uq_orgs_slug ON core.orgs (slug) WHERE deleted_at IS NULL;
DROP INDEX core.uq_memberships_org_id_user_id;
CREATE UNIQUE INDEX uq_memberships_org_id_user_id ON core.memberships (org_id, user_id) WHERE deleted_at IS NULL;
