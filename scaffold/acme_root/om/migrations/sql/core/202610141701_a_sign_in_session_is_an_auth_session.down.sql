-- Gives the sign-in sessions their earlier name back, every row kept, and
-- moves their stored values back under the same counted lift of FORCE; the
-- fence, the grants, and the indexes follow the table.

ALTER TABLE core.outbox_rows NO FORCE ROW LEVEL SECURITY;
DO $$
DECLARE
    meant bigint;
    touched bigint;
BEGIN
    SELECT count(*) INTO meant FROM core.outbox_rows WHERE starts_with(kind, 'tenancy.auth_session.');
    UPDATE core.outbox_rows SET kind = 'tenancy.session.' || substr(kind, 22) WHERE starts_with(kind, 'tenancy.auth_session.');
    GET DIAGNOSTICS touched = ROW_COUNT;
    IF touched <> meant THEN
        RAISE EXCEPTION 'core.outbox_rows: touched % rows of %', touched, meant;
    END IF;
END
$$;
ALTER TABLE core.outbox_rows FORCE ROW LEVEL SECURITY;

ALTER TABLE core.socket_tickets NO FORCE ROW LEVEL SECURITY;
DO $$
DECLARE
    meant bigint;
    touched bigint;
BEGIN
    SELECT count(*) INTO meant FROM core.socket_tickets WHERE credential_kind = 'auth_session_token';
    UPDATE core.socket_tickets SET credential_kind = 'session_token' WHERE credential_kind = 'auth_session_token';
    GET DIAGNOSTICS touched = ROW_COUNT;
    IF touched <> meant THEN
        RAISE EXCEPTION 'core.socket_tickets: touched % rows of %', touched, meant;
    END IF;
END
$$;
ALTER TABLE core.socket_tickets FORCE ROW LEVEL SECURITY;

ALTER TABLE core.auth_sessions NO FORCE ROW LEVEL SECURITY;
DO $$
DECLARE
    meant bigint;
    touched bigint;
BEGIN
    SELECT count(*) INTO meant FROM core.auth_sessions WHERE credential_kind = 'auth_session_token';
    UPDATE core.auth_sessions SET credential_kind = 'session_token' WHERE credential_kind = 'auth_session_token';
    GET DIAGNOSTICS touched = ROW_COUNT;
    IF touched <> meant THEN
        RAISE EXCEPTION 'core.auth_sessions: touched % rows of %', touched, meant;
    END IF;
END
$$;
ALTER TABLE core.auth_sessions FORCE ROW LEVEL SECURITY;

ALTER INDEX core.ix_auth_sessions_expires_at RENAME TO ix_sessions_expires_at;
ALTER INDEX core.ix_auth_sessions_org_id_expires_at RENAME TO ix_sessions_org_id_expires_at;
ALTER INDEX core.uq_auth_sessions_token_hash RENAME TO uq_sessions_token_hash;
ALTER TABLE core.auth_sessions RENAME CONSTRAINT pk_auth_sessions TO pk_sessions;
ALTER TABLE core.auth_sessions RENAME TO sessions;
