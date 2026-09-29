-- Sign-in moves to the identity provider. Expand only: every new column is
-- nullable, and the release before this one keeps working on the schema
-- while a rollout runs both.
--
-- Acme keeps no password from here on. The hash stops being read or
-- written, and it becomes nullable so this release's inserts leave it
-- empty. The release before this one still names it in every identity
-- read, so the column stays for one release; the release after drops it,
-- with the hashes it still holds.

ALTER TABLE core.identities ALTER COLUMN password_hash DROP NOT NULL;

-- The provider's name for a person: the issuer and the subject under it.
ALTER TABLE core.identities ADD COLUMN issuer text NULL;
ALTER TABLE core.identities ADD COLUMN subject text NULL;
CREATE UNIQUE INDEX uq_identities_issuer_subject ON core.identities (issuer, subject) WHERE subject IS NOT NULL;

-- An org's organization at the provider, made the first time it is needed.
ALTER TABLE core.orgs ADD COLUMN provider_org_id text NULL;
CREATE UNIQUE INDEX uq_orgs_provider_org_id ON core.orgs (provider_org_id) WHERE provider_org_id IS NOT NULL AND deleted_at IS NULL;

-- A tenant's invitations. The provider sends the email; the row holds the
-- role the person gets and where the invitation stands.
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
CREATE UNIQUE INDEX uq_invitations_provider_invitation_id ON core.invitations (provider_invitation_id);
-- One pending invitation per address in an org; the tenant leads it, so
-- it serves the tenant's list of pending invitations too.
CREATE UNIQUE INDEX uq_invitations_pending_email ON core.invitations (org_id, email) WHERE state = 'pending';

-- A tenant's rows, fenced by the tenant, as orgs are.
ALTER TABLE core.invitations ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.invitations FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_fence ON core.invitations FOR ALL
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
