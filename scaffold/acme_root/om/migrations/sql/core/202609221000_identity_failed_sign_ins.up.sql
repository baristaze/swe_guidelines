-- A person's run of failed sign-ins, and when the last one was: the sign-in
-- delay grows from them, in the tenancy role's own storage, so it holds when
-- the cache the per-address limit counts in is down. Expand only: every
-- existing identity starts with no failures.

ALTER TABLE core.identities ADD COLUMN failed_sign_ins integer NULL;
UPDATE core.identities SET failed_sign_ins = 0;
ALTER TABLE core.identities ALTER COLUMN failed_sign_ins SET NOT NULL;
ALTER TABLE core.identities ADD COLUMN last_failed_sign_in_at timestamptz NULL;
