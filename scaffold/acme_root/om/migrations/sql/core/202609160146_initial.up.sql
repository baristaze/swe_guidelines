-- The tenancy swimlane: orgs, identities, users, memberships, sessions, api keys.
-- Every table opens with its mixin header block in house-style order.

CREATE TABLE core.orgs (
    id uuid NOT NULL,
    org_id uuid NOT NULL,
    name text NOT NULL,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    created_by uuid NOT NULL,
    deleted_at timestamptz NULL,
    deleted_by uuid NULL,
    slug text NOT NULL,
    CONSTRAINT pk_orgs PRIMARY KEY (id)
);
CREATE INDEX ix_orgs_org_id ON core.orgs (org_id);
CREATE UNIQUE INDEX uq_orgs_slug ON core.orgs (slug);

CREATE TABLE core.identities (
    id uuid NOT NULL,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    created_by uuid NOT NULL,
    email text NOT NULL,
    password_hash text NOT NULL,
    is_operator boolean NOT NULL,
    CONSTRAINT pk_identities PRIMARY KEY (id)
);
CREATE UNIQUE INDEX uq_identities_email ON core.identities (email);

CREATE TABLE core.users (
    id uuid NOT NULL,
    org_id uuid NOT NULL,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    created_by uuid NOT NULL,
    deleted_at timestamptz NULL,
    deleted_by uuid NULL,
    identity_id uuid NOT NULL,
    email text NOT NULL,
    display_name text NOT NULL,
    CONSTRAINT pk_users PRIMARY KEY (id)
);
CREATE INDEX ix_users_org_id ON core.users (org_id);
CREATE INDEX ix_users_identity_id ON core.users (identity_id);

CREATE TABLE core.memberships (
    id uuid NOT NULL,
    org_id uuid NOT NULL,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    created_by uuid NOT NULL,
    user_id uuid NOT NULL,
    role text NOT NULL,
    teams jsonb NOT NULL,
    CONSTRAINT pk_memberships PRIMARY KEY (id)
);
CREATE UNIQUE INDEX uq_memberships_org_id_user_id ON core.memberships (org_id, user_id);

CREATE TABLE core.sessions (
    id uuid NOT NULL,
    org_id uuid NOT NULL,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    created_by uuid NOT NULL,
    identity_id uuid NOT NULL,
    user_id uuid NOT NULL,
    token_hash text NOT NULL,
    credential_kind text NOT NULL,
    expires_at timestamptz NOT NULL,
    revoked_at timestamptz NULL,
    CONSTRAINT pk_sessions PRIMARY KEY (id)
);
CREATE INDEX ix_sessions_org_id ON core.sessions (org_id);
CREATE UNIQUE INDEX uq_sessions_token_hash ON core.sessions (token_hash);

CREATE TABLE core.api_keys (
    id uuid NOT NULL,
    org_id uuid NOT NULL,
    name text NOT NULL,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    created_by uuid NOT NULL,
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
