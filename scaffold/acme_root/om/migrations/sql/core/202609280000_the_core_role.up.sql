-- The core role, whole: tenancy (orgs, identities, users, memberships,
-- invitations, sessions, api keys, socket tickets, sign-in delays), the
-- outbox, the idempotency markers, media's files, and the orchestrations.
--
-- Every tenant table carries `org_id` and one row-level security policy,
-- `tenant_fence`, forced on the table's owner too: a row is read or written
-- only under the transaction's own tenant, or under the system scope
-- (`EMPTY_UUID`) by the system login alone. The policies are the second
-- fence, behind the storage funnel's `org_id` (ADR 0016). `identities` and
-- `sign_in_delays` are global: they carry no tenant, and only the system
-- scope reads them.
--
-- A table opens with its mixin header in house-style order: the id, the
-- tenant, the name, the provenance, the soft delete.

CREATE TABLE core.orgs (
    id uuid NOT NULL,
    org_id uuid NOT NULL,
    name text NOT NULL,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    created_by uuid NOT NULL,
    updated_by uuid NOT NULL,
    deleted_at timestamptz NULL,
    deleted_by uuid NULL,
    slug text NOT NULL,
    kind text NOT NULL DEFAULT 'team',
    personal_identity_id uuid NULL,
    provider_org_id text NULL,
    purged_at timestamptz NULL,
    CONSTRAINT pk_orgs PRIMARY KEY (id),
    CONSTRAINT ck_orgs_personal_identity CHECK ((kind = 'personal') = (personal_identity_id IS NOT NULL))
);
CREATE UNIQUE INDEX uq_orgs_slug ON core.orgs (slug) WHERE deleted_at IS NULL;
CREATE UNIQUE INDEX uq_orgs_personal_identity_id ON core.orgs (personal_identity_id)
    WHERE kind = 'personal' AND deleted_at IS NULL;
CREATE UNIQUE INDEX uq_orgs_provider_org_id ON core.orgs (provider_org_id)
    WHERE provider_org_id IS NOT NULL AND deleted_at IS NULL;

-- One row per person, across every org. The digest is of the address folded
-- to lower case, so one address in any case is one person (ADR 0072).
CREATE TABLE core.identities (
    id uuid NOT NULL,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    created_by uuid NOT NULL,
    updated_by uuid NOT NULL,
    email text NOT NULL,
    email_digest text GENERATED ALWAYS AS (
        encode(sha256(decode(lower(email COLLATE pg_unicode_fast), 'escape')), 'hex')
    ) STORED,
    issuer text NULL,
    subject text NULL,
    operator_role text NULL,
    totp_secret text NULL,
    totp_confirmed_at timestamptz NULL,
    totp_last_step bigint NULL,
    time_zone text NULL,
    CONSTRAINT pk_identities PRIMARY KEY (id)
);
CREATE UNIQUE INDEX uq_identities_email_digest ON core.identities (email_digest);
CREATE UNIQUE INDEX uq_identities_issuer_subject ON core.identities (issuer, subject)
    WHERE subject IS NOT NULL;

CREATE TABLE core.users (
    id uuid NOT NULL,
    org_id uuid NOT NULL,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    created_by uuid NOT NULL,
    updated_by uuid NOT NULL,
    deleted_at timestamptz NULL,
    deleted_by uuid NULL,
    identity_id uuid NOT NULL,
    email text NOT NULL,
    display_name text NOT NULL,
    CONSTRAINT pk_users PRIMARY KEY (id)
);
CREATE UNIQUE INDEX uq_users_org_id_identity_id_live ON core.users (org_id, identity_id)
    WHERE deleted_at IS NULL;
CREATE INDEX ix_users_org_id_deleted_at ON core.users (org_id, deleted_at);
CREATE INDEX ix_users_deleted_at ON core.users (deleted_at) WHERE deleted_at IS NOT NULL;
CREATE INDEX ix_users_identity_id ON core.users (identity_id);

CREATE TABLE core.memberships (
    id uuid NOT NULL,
    org_id uuid NOT NULL,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    created_by uuid NOT NULL,
    updated_by uuid NOT NULL,
    deleted_at timestamptz NULL,
    deleted_by uuid NULL,
    user_id uuid NOT NULL,
    role text NOT NULL,
    teams jsonb NOT NULL,
    CONSTRAINT pk_memberships PRIMARY KEY (id)
);
CREATE UNIQUE INDEX uq_memberships_org_id_user_id ON core.memberships (org_id, user_id)
    WHERE deleted_at IS NULL;
CREATE INDEX ix_memberships_org_id_deleted_at ON core.memberships (org_id, deleted_at);
CREATE INDEX ix_memberships_deleted_at ON core.memberships (deleted_at)
    WHERE deleted_at IS NOT NULL;

CREATE TABLE core.invitations (
    id uuid NOT NULL,
    org_id uuid NOT NULL,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    created_by uuid NOT NULL,
    updated_by uuid NOT NULL,
    email text NOT NULL,
    role text NOT NULL,
    provider_invitation_id text NOT NULL,
    state text NOT NULL,
    expires_at timestamptz NOT NULL,
    accepted_user_id uuid NULL,
    CONSTRAINT pk_invitations PRIMARY KEY (id)
);
CREATE UNIQUE INDEX uq_invitations_pending_email ON core.invitations (org_id, email)
    WHERE state = 'pending';
CREATE UNIQUE INDEX uq_invitations_provider_invitation_id
    ON core.invitations (provider_invitation_id);
CREATE INDEX ix_invitations_org_id_expires_at ON core.invitations (org_id, expires_at);
CREATE INDEX ix_invitations_expires_at ON core.invitations (expires_at);
CREATE INDEX ix_invitations_updated_at ON core.invitations (updated_at);

-- A login credential, an operator token (no tenant: under the system
-- scope), or a tenant session. Only the token's hash is kept.
CREATE TABLE core.sessions (
    id uuid NOT NULL,
    org_id uuid NOT NULL,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    created_by uuid NOT NULL,
    updated_by uuid NOT NULL,
    identity_id uuid NOT NULL,
    user_id uuid NOT NULL,
    token_hash text NOT NULL,
    credential_kind text NOT NULL,
    expires_at timestamptz NOT NULL,
    revoked_at timestamptz NULL,
    last_seen_at timestamptz NULL,
    second_factor_at timestamptz NULL,
    operator_role text NULL,
    provider_session_id text NULL,
    CONSTRAINT pk_sessions PRIMARY KEY (id)
);
CREATE UNIQUE INDEX uq_sessions_token_hash ON core.sessions (token_hash);
CREATE INDEX ix_sessions_org_id_expires_at ON core.sessions (org_id, expires_at);
CREATE INDEX ix_sessions_expires_at ON core.sessions (expires_at);

-- A key hash digests a fresh random secret, and the lookup reads revoked
-- keys too, so it can answer "revoked": the unique index is full (ADR 0019).
CREATE TABLE core.api_keys (
    id uuid NOT NULL,
    org_id uuid NOT NULL,
    name text NOT NULL,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    created_by uuid NOT NULL,
    updated_by uuid NOT NULL,
    deleted_at timestamptz NULL,
    deleted_by uuid NULL,
    user_id uuid NOT NULL,
    key_hash text NOT NULL,
    role text NOT NULL,
    expires_at timestamptz NOT NULL,
    CONSTRAINT pk_api_keys PRIMARY KEY (id)
);
CREATE INDEX ix_api_keys_org_id ON core.api_keys (org_id);
CREATE UNIQUE INDEX uq_api_keys_key_hash ON core.api_keys (key_hash);
CREATE INDEX ix_api_keys_deleted_at ON core.api_keys (deleted_at) WHERE deleted_at IS NOT NULL;
CREATE INDEX ix_api_keys_expires_at ON core.api_keys (expires_at);

CREATE TABLE core.socket_tickets (
    id uuid NOT NULL,
    org_id uuid NOT NULL,
    created_at timestamptz NOT NULL,
    user_id uuid NOT NULL,
    ticket_hash text NOT NULL,
    credential_kind text NOT NULL,
    credential_id uuid NOT NULL,
    expires_at timestamptz NOT NULL,
    redeemed_at timestamptz NULL,
    CONSTRAINT pk_socket_tickets PRIMARY KEY (id)
);
CREATE UNIQUE INDEX uq_socket_tickets_ticket_hash ON core.socket_tickets (ticket_hash);
CREATE INDEX ix_socket_tickets_org_id_expires_at ON core.socket_tickets (org_id, expires_at);
CREATE INDEX ix_socket_tickets_expires_at ON core.socket_tickets (expires_at);

-- The failed sign-ins of one address, by its digest: global, since a sign-in
-- names no tenant yet.
CREATE TABLE core.sign_in_delays (
    id uuid NOT NULL,
    email_digest text NOT NULL,
    failures integer NOT NULL,
    last_failed_at timestamptz NOT NULL,
    CONSTRAINT pk_sign_in_delays PRIMARY KEY (id)
);
CREATE UNIQUE INDEX uq_sign_in_delays_email_digest ON core.sign_in_delays (email_digest);

-- The outbox: every core write lands its rows here in its own commit, and
-- the relay moves them to the stream and the queue.
CREATE TABLE core.outbox_rows (
    id uuid NOT NULL,
    org_id uuid NOT NULL,
    created_at timestamptz NOT NULL,
    kind text NOT NULL,
    target_id uuid NOT NULL,
    payload jsonb NOT NULL,
    actor_id uuid NOT NULL,
    request_id uuid NOT NULL,
    app text NOT NULL,
    traceparent text NULL,
    done_at timestamptz NULL,
    attempts integer NOT NULL,
    next_attempt_at timestamptz NULL,
    last_error text NULL,
    failed_at timestamptz NULL,
    CONSTRAINT pk_outbox_rows PRIMARY KEY (id)
);
CREATE INDEX ix_outbox_rows_done_at_id ON core.outbox_rows (done_at, id);
CREATE INDEX ix_outbox_rows_failed_at ON core.outbox_rows (failed_at) WHERE failed_at IS NOT NULL;

-- The idempotency markers. The re-mint fence reads a pending marker by its
-- attempt, so the pending ones carry an index of their own.
CREATE TABLE core.idempotency_records (
    id uuid NOT NULL,
    org_id uuid NOT NULL,
    created_at timestamptz NOT NULL,
    user_id uuid NOT NULL,
    key text NOT NULL,
    request_digest text NOT NULL,
    target_id uuid NOT NULL,
    attempt_id uuid NULL,
    status integer NULL,
    body text NULL,
    CONSTRAINT pk_idempotency_records PRIMARY KEY (id)
);
CREATE UNIQUE INDEX uq_idempotency_records_org_id_user_id_key
    ON core.idempotency_records (org_id, user_id, key);
CREATE INDEX ix_idempotency_records_created_at ON core.idempotency_records (created_at);
CREATE INDEX ix_idempotency_records_attempt_id ON core.idempotency_records (attempt_id) WHERE status IS NULL;

-- Media: a reference to one object in the store, never its bytes.
CREATE TABLE core.files (
    id uuid NOT NULL,
    org_id uuid NOT NULL,
    name text NOT NULL,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    created_by uuid NOT NULL,
    updated_by uuid NOT NULL,
    deleted_at timestamptz NULL,
    deleted_by uuid NULL,
    key text NOT NULL,
    extension text NOT NULL,
    content_type text NOT NULL,
    size_bytes bigint NOT NULL,
    purpose text NOT NULL,
    subject_id uuid NULL,
    status text NOT NULL,
    CONSTRAINT pk_files PRIMARY KEY (id)
);
CREATE INDEX ix_files_org_id_purpose_subject_id_id ON core.files (org_id, purpose, subject_id, id);
CREATE INDEX ix_files_org_id_deleted_at ON core.files (org_id, deleted_at);
CREATE INDEX ix_files_org_id_status_created_at ON core.files (org_id, status, created_at);
CREATE INDEX ix_files_deleted_at ON core.files (deleted_at) WHERE deleted_at IS NOT NULL;
CREATE INDEX ix_files_created_at_pending ON core.files (created_at) WHERE deleted_at IS NULL AND status = 'pending';

-- The long-running records. A record kept per period is unique by its org,
-- its kind, and the period.
CREATE TABLE core.orchestrations (
    id uuid NOT NULL,
    org_id uuid NOT NULL,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    created_by uuid NOT NULL,
    updated_by uuid NOT NULL,
    kind text NOT NULL,
    input jsonb NOT NULL,
    period text NULL,
    status text NOT NULL,
    cursor integer NOT NULL,
    total integer NULL,
    applied integer NOT NULL,
    skipped integer NOT NULL,
    row_errors jsonb NOT NULL,
    park_reason text NULL,
    fail_reason text NULL,
    fail_detail text NULL,
    finished_at timestamptz NULL,
    version integer NOT NULL,
    CONSTRAINT pk_orchestrations PRIMARY KEY (id)
);
CREATE UNIQUE INDEX uq_orchestrations_org_id_kind_period ON core.orchestrations (org_id, kind, period)
    WHERE period IS NOT NULL;
CREATE INDEX ix_orchestrations_org_id_kind_id ON core.orchestrations (org_id, kind, id);
CREATE INDEX ix_orchestrations_org_id_status_updated_at
    ON core.orchestrations (org_id, status, updated_at);
CREATE INDEX ix_orchestrations_status_updated_at ON core.orchestrations (status, updated_at);

-- The second fence. A tenant table admits a row of the transaction's own
-- tenant, or any row under the system scope to the system login alone.
-- Tables a person's own credentials live in narrow the tenant further to the
-- user the transaction names, when it names one; `users` narrows to the
-- identity the same way.

ALTER TABLE core.orgs ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.orgs FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_fence ON core.orgs
    USING (
        org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
        OR (
            current_setting('app.org_id', true) = '00000000-0000-0000-0000-000000000000'
            AND current_user = 'acme_system'
        )
    )
    WITH CHECK (
        org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
        OR (
            current_setting('app.org_id', true) = '00000000-0000-0000-0000-000000000000'
            AND current_user = 'acme_system'
        )
    );

ALTER TABLE core.users ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.users FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_fence ON core.users
    USING (
        (
            org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
            OR (
                current_setting('app.org_id', true) = '00000000-0000-0000-0000-000000000000'
                AND current_user = 'acme_system'
            )
        )
        AND (
            current_setting('app.identity_id', true) IS NULL
            OR current_setting('app.identity_id', true) = ''
            OR identity_id = NULLIF(current_setting('app.identity_id', true), '')::uuid
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
            current_setting('app.identity_id', true) IS NULL
            OR current_setting('app.identity_id', true) = ''
            OR identity_id = NULLIF(current_setting('app.identity_id', true), '')::uuid
        )
    );

ALTER TABLE core.memberships ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.memberships FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_fence ON core.memberships
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

ALTER TABLE core.invitations ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.invitations FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_fence ON core.invitations
    USING (
        org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
        OR (
            current_setting('app.org_id', true) = '00000000-0000-0000-0000-000000000000'
            AND current_user = 'acme_system'
        )
    )
    WITH CHECK (
        org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
        OR (
            current_setting('app.org_id', true) = '00000000-0000-0000-0000-000000000000'
            AND current_user = 'acme_system'
        )
    );

ALTER TABLE core.sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.sessions FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_fence ON core.sessions
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

ALTER TABLE core.api_keys ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.api_keys FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_fence ON core.api_keys
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

ALTER TABLE core.socket_tickets ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.socket_tickets FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_fence ON core.socket_tickets
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

ALTER TABLE core.outbox_rows ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.outbox_rows FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_fence ON core.outbox_rows
    USING (
        org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
        OR (
            current_setting('app.org_id', true) = '00000000-0000-0000-0000-000000000000'
            AND current_user = 'acme_system'
        )
    )
    WITH CHECK (
        org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
        OR (
            current_setting('app.org_id', true) = '00000000-0000-0000-0000-000000000000'
            AND current_user = 'acme_system'
        )
    );

ALTER TABLE core.idempotency_records ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.idempotency_records FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_fence ON core.idempotency_records
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

ALTER TABLE core.files ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.files FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_fence ON core.files
    USING (
        org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
        OR (
            current_setting('app.org_id', true) = '00000000-0000-0000-0000-000000000000'
            AND current_user = 'acme_system'
        )
    )
    WITH CHECK (
        org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
        OR (
            current_setting('app.org_id', true) = '00000000-0000-0000-0000-000000000000'
            AND current_user = 'acme_system'
        )
    );

ALTER TABLE core.orchestrations ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.orchestrations FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_fence ON core.orchestrations
    USING (
        org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
        OR (
            current_setting('app.org_id', true) = '00000000-0000-0000-0000-000000000000'
            AND current_user = 'acme_system'
        )
    )
    WITH CHECK (
        org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
        OR (
            current_setting('app.org_id', true) = '00000000-0000-0000-0000-000000000000'
            AND current_user = 'acme_system'
        )
    );

-- The runtime and the system logins hold DML on every table of the role and
-- nothing else: no table is theirs, so neither can drop a policy, turn FORCE
-- off, or alter a table. Every table the migration login creates from here on
-- grants the same, by default privilege. The migration bookkeeping is the
-- migration login's alone. `migrate ensure-logins` makes the logins and runs
-- the same grants, so it runs before this.

GRANT USAGE ON SCHEMA core TO acme_runtime, acme_system;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA core TO acme_runtime, acme_system;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA core TO acme_runtime, acme_system;
ALTER DEFAULT PRIVILEGES FOR ROLE acme_migration IN SCHEMA core
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO acme_runtime, acme_system;
ALTER DEFAULT PRIVILEGES FOR ROLE acme_migration IN SCHEMA core
    GRANT USAGE, SELECT ON SEQUENCES TO acme_runtime, acme_system;
REVOKE ALL ON core.alembic_version FROM acme_runtime, acme_system;
