-- The operator plane's second factor, the operator token, a session's idle
-- lifetime, and the sign-in delay keyed on the email rather than on the
-- identity. Expand only: every new column is nullable or computed, so the
-- release before this one keeps working on it while a rollout runs both.
--
-- The email digest is a generated column, so a row an older release writes
-- carries it too and the sign-in lookup finds every identity. A generated
-- column takes only immutable functions: `decode(email, 'escape')` is the
-- immutable spelling of the address's UTF-8 bytes (an address holds no
-- backslash, which it would read as an escape).

ALTER TABLE core.identities ADD COLUMN email_digest text GENERATED ALWAYS AS (encode(sha256(decode(email, 'escape')), 'hex')) STORED;
CREATE UNIQUE INDEX uq_identities_email_digest ON core.identities (email_digest);
ALTER TABLE core.identities ADD COLUMN totp_secret text NULL;
ALTER TABLE core.identities ADD COLUMN totp_confirmed_at timestamptz NULL;
ALTER TABLE core.identities ADD COLUMN totp_last_step bigint NULL;

-- The run of failed sign-ins moves to its own table. The two columns stay
-- one release, with a default so this release's inserts need not name them;
-- the next release drops them.
ALTER TABLE core.identities ALTER COLUMN failed_sign_ins SET DEFAULT 0;

ALTER TABLE core.sessions ADD COLUMN last_seen_at timestamptz NULL;
ALTER TABLE core.sessions ADD COLUMN second_factor_at timestamptz NULL;
ALTER TABLE core.sessions ADD COLUMN operator_role text NULL;

-- A system table, like identities: keyed on an email before any identity is
-- known, so it has no tenant, no policy, and no row-level security.
CREATE TABLE core.sign_in_delays (
    id uuid NOT NULL,
    email_digest text NOT NULL,
    failures integer NOT NULL,
    last_failed_at timestamptz NOT NULL,
    CONSTRAINT pk_sign_in_delays PRIMARY KEY (id)
);
CREATE UNIQUE INDEX uq_sign_in_delays_email_digest ON core.sign_in_delays (email_digest);
