-- The Slack app an org installs, and the one-time states an install carries
-- through Slack and back. Both are a tenant's rows and carry the tenant
-- fence; the two lookups that start from what Slack sends (a workspace, a
-- state) read in the system scope.
--
-- An installation is unique among the living twice: one per org, and one
-- org per workspace. Its bot token is not here: `credential_ref` names the
-- org's own secret that holds it. A state is kept as its digest, unique.
--
-- The expand half. The channel linked by a one-time code
-- (`slack_connections`, `slack_link_codes`) stays, unread, for the release
-- before this one to serve during the rollout; the release after this one
-- drops both. Their rows are not carried over: a code-linked channel holds no
-- token, so each org installs the app again.

CREATE TABLE core.slack_installations (
    id uuid NOT NULL,
    org_id uuid NOT NULL,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    created_by uuid NOT NULL,
    updated_by uuid NOT NULL,
    deleted_at timestamptz NULL,
    deleted_by uuid NULL,
    team_id text NOT NULL,
    team_name text NOT NULL,
    app_id text NOT NULL,
    bot_user_id text NOT NULL,
    scopes text NOT NULL,
    installed_by_slack_user text NOT NULL,
    credential_ref text NOT NULL,
    token_expires_at timestamptz NULL,
    refreshing_until timestamptz NULL,
    channel_id text NULL,
    status text NOT NULL,
    broken_reason text NULL,
    CONSTRAINT pk_slack_installations PRIMARY KEY (id)
);
CREATE UNIQUE INDEX uq_slack_installations_org_id ON core.slack_installations (org_id)
    WHERE deleted_at IS NULL;
CREATE UNIQUE INDEX uq_slack_installations_team_id ON core.slack_installations (team_id)
    WHERE deleted_at IS NULL;

CREATE TABLE core.slack_install_states (
    id uuid NOT NULL,
    org_id uuid NOT NULL,
    created_at timestamptz NOT NULL,
    user_id uuid NOT NULL,
    state_hash text NOT NULL,
    expires_at timestamptz NOT NULL,
    redeemed_at timestamptz NULL,
    CONSTRAINT pk_slack_install_states PRIMARY KEY (id)
);
CREATE INDEX ix_slack_install_states_org_id ON core.slack_install_states (org_id);
CREATE UNIQUE INDEX uq_slack_install_states_state_hash
    ON core.slack_install_states (state_hash);

ALTER TABLE core.slack_installations ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.slack_installations FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_fence ON core.slack_installations FOR ALL
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

ALTER TABLE core.slack_install_states ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.slack_install_states FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_fence ON core.slack_install_states FOR ALL
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
