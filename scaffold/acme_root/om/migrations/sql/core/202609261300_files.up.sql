-- The media swimlane: one row per file a tenant keeps in the object store,
-- a reference and never the bytes. A subject's files are read by id, the
-- sweep reads the deleted rows and the abandoned uploads, and the usage sums
-- the live rows; each read leads with org_id, so org_id gets no index of its
-- own. The size is a bigint because a sum of sizes is.

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

-- A tenant's rows: the second fence on org_id, and the system scope for the
-- system login alone, as every other org-scoped table of the role.
ALTER TABLE core.files ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.files FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_fence ON core.files FOR ALL
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
