-- The store's id of a file's multipart upload, set when the upload starts.
-- An existing row names none, and reads as a pending file with no upload
-- under way; nothing else changes.

ALTER TABLE core.files ADD COLUMN upload_id text NULL;
