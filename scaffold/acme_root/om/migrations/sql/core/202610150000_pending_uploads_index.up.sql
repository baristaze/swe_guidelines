-- The sweep reads the live uploads never confirmed by their birth. The read
-- names the status as a literal, the one this predicate names, so a
-- prepared statement's generic plan proves the predicate and reads the
-- index. The index then needs no status column and holds the pending
-- uploads alone: a confirm, which moves an upload to stored, writes it no
-- entry. Plain CREATE INDEX: the runner applies a role's chain in one
-- transaction, where CONCURRENTLY is refused. The build comes before the
-- drop.
CREATE INDEX ix_files_created_at_pending ON core.files (created_at) WHERE deleted_at IS NULL AND status = 'pending';
DROP INDEX core.ix_files_status_created_at;
