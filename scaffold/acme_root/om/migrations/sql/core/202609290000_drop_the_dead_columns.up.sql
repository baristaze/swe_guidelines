-- The contract half of ADR 0038.
--
-- Acme keeps no password, and the run of failed sign-ins is
-- `sign_in_delays`. A task is due on a date, and its due time is gone from
-- the wire and from every write. The release before this one (202609280000)
-- took the four columns out of the mapping, so no statement it runs names
-- them, and a migration runs before the services roll. They go now, with
-- whatever they hold: the hashes are already cleared, and nothing reads the
-- rest.

ALTER TABLE core.identities DROP COLUMN password_hash;
ALTER TABLE core.identities DROP COLUMN failed_sign_ins;
ALTER TABLE core.identities DROP COLUMN last_failed_sign_in_at;
ALTER TABLE core.tasks DROP COLUMN remind_at;
