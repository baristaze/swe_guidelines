-- A sign-in session takes the name auth session. A rename keeps every row,
-- the grants, and the row-level security, which follow the table; the
-- constraint and the indexes take the table's new name. The fence is spelled
-- again on the new name, so the chain names every table it holds.
--
-- The stored values that name it move in place: the credential kind
-- `session_token` on the sessions and the socket tickets, and the outbox
-- kinds `tenancy.session.*`. FORCE binds the migration login that owns the
-- tables, so each move lifts it for its own statement, counts the rows it
-- means to touch, fails on any other count, and puts it back, all in the
-- migration's one transaction.

ALTER TABLE core.sessions RENAME TO auth_sessions;
ALTER TABLE core.auth_sessions RENAME CONSTRAINT pk_sessions TO pk_auth_sessions;
ALTER INDEX core.uq_sessions_token_hash RENAME TO uq_auth_sessions_token_hash;
ALTER INDEX core.ix_sessions_org_id_expires_at RENAME TO ix_auth_sessions_org_id_expires_at;
ALTER INDEX core.ix_sessions_expires_at RENAME TO ix_auth_sessions_expires_at;

ALTER TABLE core.auth_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.auth_sessions FORCE ROW LEVEL SECURITY;
DROP POLICY tenant_fence ON core.auth_sessions;
CREATE POLICY tenant_fence ON core.auth_sessions
    USING (
        (
            org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
            OR (
                current_setting('app.org_id', true) = '00000000-0000-0000-0000-000000000000'
                AND current_user = 'acme_system'
            )
        )
        AND (
            current_setting('app.user_id', true) IS NULL
            OR current_setting('app.user_id', true) = ''
            OR user_id = NULLIF(current_setting('app.user_id', true), '')::uuid
        )
    )
    WITH CHECK (
        (
            org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
            OR (
                current_setting('app.org_id', true) = '00000000-0000-0000-0000-000000000000'
                AND current_user = 'acme_system'
            )
        )
        AND (
            current_setting('app.user_id', true) IS NULL
            OR current_setting('app.user_id', true) = ''
            OR user_id = NULLIF(current_setting('app.user_id', true), '')::uuid
        )
    );

ALTER TABLE core.auth_sessions NO FORCE ROW LEVEL SECURITY;
DO $$
DECLARE
    meant bigint;
    touched bigint;
BEGIN
    SELECT count(*) INTO meant FROM core.auth_sessions WHERE credential_kind = 'session_token';
    UPDATE core.auth_sessions SET credential_kind = 'auth_session_token' WHERE credential_kind = 'session_token';
    GET DIAGNOSTICS touched = ROW_COUNT;
    IF touched <> meant THEN
        RAISE EXCEPTION 'core.auth_sessions: touched % rows of %', touched, meant;
    END IF;
END
$$;
ALTER TABLE core.auth_sessions FORCE ROW LEVEL SECURITY;

ALTER TABLE core.socket_tickets NO FORCE ROW LEVEL SECURITY;
DO $$
DECLARE
    meant bigint;
    touched bigint;
BEGIN
    SELECT count(*) INTO meant FROM core.socket_tickets WHERE credential_kind = 'session_token';
    UPDATE core.socket_tickets SET credential_kind = 'auth_session_token' WHERE credential_kind = 'session_token';
    GET DIAGNOSTICS touched = ROW_COUNT;
    IF touched <> meant THEN
        RAISE EXCEPTION 'core.socket_tickets: touched % rows of %', touched, meant;
    END IF;
END
$$;
ALTER TABLE core.socket_tickets FORCE ROW LEVEL SECURITY;

ALTER TABLE core.outbox_rows NO FORCE ROW LEVEL SECURITY;
DO $$
DECLARE
    meant bigint;
    touched bigint;
BEGIN
    SELECT count(*) INTO meant FROM core.outbox_rows WHERE starts_with(kind, 'tenancy.session.');
    UPDATE core.outbox_rows SET kind = 'tenancy.auth_session.' || substr(kind, 17) WHERE starts_with(kind, 'tenancy.session.');
    GET DIAGNOSTICS touched = ROW_COUNT;
    IF touched <> meant THEN
        RAISE EXCEPTION 'core.outbox_rows: touched % rows of %', touched, meant;
    END IF;
END
$$;
ALTER TABLE core.outbox_rows FORCE ROW LEVEL SECURITY;
