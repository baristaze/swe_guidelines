-- Narrowing back is safe only below 2^31, which is where the column was.
ALTER TABLE activity.events ALTER COLUMN seq TYPE integer;
