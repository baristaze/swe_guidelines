-- The two tables come back empty, in the shape the release before them
-- read, with the tenant fence. Their rows and the password hashes are gone:
-- a downgrade restores the shape, not the data. An identity keeps a NULL
-- hash, which the downgrade of 202609261200 turns into one no password
-- matches.

CREATE TABLE core.slack_connections (
    id uuid NOT NULL,
    org_id uuid NOT NULL,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    created_by uuid NOT NULL,
    updated_by uuid NOT NULL,
    deleted_at timestamptz NULL,
    deleted_by uuid NULL,
    team_id text NOT NULL,
    channel_id text NOT NULL,
    linked_by_slack_user text NOT NULL,
    status text NOT NULL,
    broken_reason text NULL,
    CONSTRAINT pk_slack_connections PRIMARY KEY (id)
);
CREATE UNIQUE INDEX uq_slack_connections_org_id ON core.slack_connections (org_id)
    WHERE deleted_at IS NULL;
CREATE UNIQUE INDEX uq_slack_connections_team_id_channel_id
    ON core.slack_connections (team_id, channel_id) WHERE deleted_at IS NULL;

CREATE TABLE core.slack_link_codes (
    id uuid NOT NULL,
    org_id uuid NOT NULL,
    created_at timestamptz NOT NULL,
    user_id uuid NOT NULL,
    code_hash text NOT NULL,
    expires_at timestamptz NOT NULL,
    redeemed_at timestamptz NULL,
    CONSTRAINT pk_slack_link_codes PRIMARY KEY (id)
);
CREATE INDEX ix_slack_link_codes_org_id ON core.slack_link_codes (org_id);
CREATE UNIQUE INDEX uq_slack_link_codes_code_hash ON core.slack_link_codes (code_hash);

ALTER TABLE core.slack_connections ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.slack_connections FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_fence ON core.slack_connections FOR ALL
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

ALTER TABLE core.slack_link_codes ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.slack_link_codes FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_fence ON core.slack_link_codes FOR ALL
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
