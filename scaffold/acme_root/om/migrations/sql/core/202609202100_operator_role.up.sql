-- The allowlist entry says what the operator may do, not only that they are
-- one: `operator_role` is `read` or `write` (write includes read) and null
-- for a person who is not an operator. Every operator so far was granted
-- everything, so the backfill says `write`. The column is renamed in one
-- step, add, backfill, drop, which ADR 0006 allows before the first
-- deployment; from then on a rename is two migrations in two releases.

ALTER TABLE core.identities ADD COLUMN operator_role text NULL;
UPDATE core.identities SET operator_role = 'write' WHERE is_operator;
ALTER TABLE core.identities DROP COLUMN is_operator;
