-- Back to the flag: an entry of either role was an operator. The default
-- fills the rows the add finds and is dropped at once, so the column reads
-- as the initial migration wrote it.

ALTER TABLE core.identities ADD COLUMN is_operator boolean NOT NULL DEFAULT false;
UPDATE core.identities SET is_operator = true WHERE operator_role IS NOT NULL;
ALTER TABLE core.identities ALTER COLUMN is_operator DROP DEFAULT;
ALTER TABLE core.identities DROP COLUMN operator_role;
