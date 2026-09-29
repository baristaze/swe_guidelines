CREATE INDEX ix_files_status_created_at ON core.files (status, created_at) WHERE deleted_at IS NULL;
DROP INDEX core.ix_files_created_at_pending;
