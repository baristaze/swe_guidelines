-- Every person gets their personal org: the org, their user in it, and the
-- owner membership, the rows `creates.personal_rows` builds. A person is every
-- identity but the platform's own (the reserved domain of
-- `rules.PLATFORM_EMAIL_DOMAIN`, which no person signs in as) and but one
-- already in as many orgs as a person may join (the managers' default bound
-- of 100), where one more place would make every read of their places refuse.
-- The org is named after the person's name where they gave one (their first
-- live user's) or "Personal", the slug is that name with a tail from the
-- identity's id, and the rows are the person's own: the provenance names the
-- new user, as a sign-up's does. The ids are minted here, since a data
-- migration has no object model above it.
--
-- Idempotent: it selects only people without a living personal org, so a
-- rerun finds nobody. Bounded: one pass takes at most 50000 people into a
-- temporary table and inserts from it in three statements; the last
-- statement refuses a pass that left anyone behind, and the whole
-- transaction rolls back, the fence included.
--
-- A migration names no tenant and FORCE binds the login that owns the
-- tables, so the fence is lifted for these statements and put back after.
-- Under the fence the reads of the orgs and the users would see nothing and
-- the inserts would be refused; the fence is never half on.

ALTER TABLE core.orgs NO FORCE ROW LEVEL SECURITY;
ALTER TABLE core.users NO FORCE ROW LEVEL SECURITY;
ALTER TABLE core.memberships NO FORCE ROW LEVEL SECURITY;

CREATE TEMP TABLE personal_backfill ON COMMIT DROP AS
SELECT
    people.identity_id,
    people.email,
    uuidv7() AS org_id,
    uuidv7() AS user_id,
    uuidv7() AS membership_id,
    coalesce(nullif(people.given_name, ''), 'Personal') AS org_name,
    coalesce(nullif(people.given_name, ''), split_part(people.email, '@', 1)) AS display_name
FROM (
    SELECT
        i.id AS identity_id,
        i.email,
        btrim(coalesce((
            SELECT u.display_name FROM core.users u
            WHERE u.identity_id = i.id AND u.deleted_at IS NULL
            ORDER BY u.id LIMIT 1
        ), '')) AS given_name
    FROM core.identities i
    WHERE lower(i.email) NOT LIKE '%@platform.acme.invalid'
        AND NOT EXISTS (
            SELECT 1 FROM core.orgs o
            WHERE o.personal_identity_id = i.id AND o.kind = 'personal' AND o.deleted_at IS NULL
        )
        AND (SELECT count(*) FROM core.users u WHERE u.identity_id = i.id AND u.deleted_at IS NULL) < 100
    ORDER BY i.id
    LIMIT 50000
) people;

INSERT INTO core.orgs (id, org_id, name, created_at, updated_at, created_by, updated_by, slug, kind, personal_identity_id)
SELECT
    b.org_id, b.org_id, b.org_name, now(), now(), b.user_id, b.user_id,
    coalesce(nullif(rtrim(left(btrim(regexp_replace(lower(b.org_name), '[^a-z0-9]+', '-', 'g'), '-'), 39), '-'), ''), 'org')
        || '-' || left(md5(b.identity_id::text), 8),
    'personal', b.identity_id
FROM personal_backfill b;

INSERT INTO core.users (id, org_id, created_at, updated_at, created_by, updated_by, identity_id, email, display_name)
SELECT b.user_id, b.org_id, now(), now(), b.user_id, b.user_id, b.identity_id, b.email, b.display_name
FROM personal_backfill b;

INSERT INTO core.memberships (id, org_id, created_at, updated_at, created_by, updated_by, user_id, role, teams)
SELECT b.membership_id, b.org_id, now(), now(), b.user_id, b.user_id, b.user_id, 'owner', '[]'::jsonb
FROM personal_backfill b;

-- Nobody is left: a person still without a personal org fails the cast, and
-- the error names how many.
SELECT CASE WHEN count(*) > 0 THEN ('personal org backfill left ' || count(*) || ' people behind')::int END
FROM core.identities i
WHERE lower(i.email) NOT LIKE '%@platform.acme.invalid'
    AND NOT EXISTS (
        SELECT 1 FROM core.orgs o
        WHERE o.personal_identity_id = i.id AND o.kind = 'personal' AND o.deleted_at IS NULL
    )
    AND (SELECT count(*) FROM core.users u WHERE u.identity_id = i.id AND u.deleted_at IS NULL) < 100;

ALTER TABLE core.orgs FORCE ROW LEVEL SECURITY;
ALTER TABLE core.users FORCE ROW LEVEL SECURITY;
ALTER TABLE core.memberships FORCE ROW LEVEL SECURITY;
