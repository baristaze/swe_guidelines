-- An address is one address in any case (ADR 0072): every stored address is
-- folded to lower case, by Unicode's full mapping, which `lower` gives under
-- the builtin `pg_unicode_fast` collation whatever the database's locale,
-- and which `rules.fold_email` gives in the process. Then the identity's
-- digest is computed from the folded address, so a writer that does not
-- fold, the release before among them, meets the unique index with a second
-- spelling instead of making a second person.
--
-- It stops first, and changes nothing, when two rows would fold to one:
-- two identities, or two pending invitations of one org. The error names
-- their ids; which one stays is a person's call, never a migration's.
--
-- The rows it means to touch are counted first and compared with the rows
-- each update reports; a difference fails the cast, and the whole
-- transaction rolls back, the fence included. A migration names no tenant
-- and FORCE binds the login that owns the tables, so the fence is lifted
-- for these statements and put back after. The identities are the system
-- scope's and carry no fence.

ALTER TABLE core.users NO FORCE ROW LEVEL SECURITY;
ALTER TABLE core.invitations NO FORCE ROW LEVEL SECURITY;

SELECT ('identities whose addresses fold to one: ' || string_agg(ids, ' and '))::int
FROM (
    SELECT '(' || string_agg(id::text, ', ' ORDER BY id) || ')' AS ids
    FROM core.identities
    GROUP BY lower(email COLLATE pg_unicode_fast)
    HAVING count(*) > 1
) clashes
HAVING count(*) > 0;

SELECT ('pending invitations of one org whose addresses fold to one: ' || string_agg(ids, ' and '))::int
FROM (
    SELECT '(' || string_agg(id::text, ', ' ORDER BY id) || ')' AS ids
    FROM core.invitations
    WHERE state = 'pending'
    GROUP BY org_id, lower(email COLLATE pg_unicode_fast)
    HAVING count(*) > 1
) clashes
HAVING count(*) > 0;

CREATE TEMP TABLE address_fold ON COMMIT DROP AS
SELECT
    (SELECT count(*) FROM core.identities
        WHERE email <> lower(email COLLATE pg_unicode_fast)) AS identities,
    (SELECT count(*) FROM core.users
        WHERE email <> lower(email COLLATE pg_unicode_fast)) AS users,
    (SELECT count(*) FROM core.invitations
        WHERE email <> lower(email COLLATE pg_unicode_fast)) AS invitations;

WITH touched AS (
    UPDATE core.identities SET email = lower(email COLLATE pg_unicode_fast)
    WHERE email <> lower(email COLLATE pg_unicode_fast)
    RETURNING 1
)
SELECT CASE WHEN t.count <> f.identities
    THEN ('address fold touched ' || t.count || ' identities of ' || f.identities)::int END
FROM (SELECT count(*) AS count FROM touched) t, address_fold f;

WITH touched AS (
    UPDATE core.users SET email = lower(email COLLATE pg_unicode_fast)
    WHERE email <> lower(email COLLATE pg_unicode_fast)
    RETURNING 1
)
SELECT CASE WHEN t.count <> f.users
    THEN ('address fold touched ' || t.count || ' users of ' || f.users)::int END
FROM (SELECT count(*) AS count FROM touched) t, address_fold f;

WITH touched AS (
    UPDATE core.invitations SET email = lower(email COLLATE pg_unicode_fast)
    WHERE email <> lower(email COLLATE pg_unicode_fast)
    RETURNING 1
)
SELECT CASE WHEN t.count <> f.invitations
    THEN ('address fold touched ' || t.count || ' invitations of ' || f.invitations)::int END
FROM (SELECT count(*) AS count FROM touched) t, address_fold f;

ALTER TABLE core.identities ALTER COLUMN email_digest
    SET EXPRESSION AS (encode(sha256(decode(lower(email COLLATE pg_unicode_fast), 'escape')), 'hex'));

ALTER TABLE core.users FORCE ROW LEVEL SECURITY;
ALTER TABLE core.invitations FORCE ROW LEVEL SECURITY;
